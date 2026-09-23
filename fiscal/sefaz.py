"""
Consulta ao SEFAZ pra detectar NF-e canceladas — webservice NFeConsultaProtocolo
(consulta de situação por chave de acesso, mod. 55, NT 4.00). Ver o plano
completo em memory/sefaz-cancelamento-plano.md (achados, decisões e ordem de
implementação).

Autenticação é só mTLS com o certificado A1 (.pfx) do CNPJ da Zanattex dono
da nota — não tem assinatura XML aqui (isso só é exigido na autorização/emissão,
não numa consulta de leitura). Certificados vêm de
settings.FISCAL_SEFAZ_CERTIFICADOS_JSON (nunca em disco, nunca no banco), um
por CNPJ; CNPJ sem certificado cadastrado vira NAO_VERIFICADA (nunca erro que
travaria a importação) — é assim que o rollout em fases funciona (só a
Zanattex tem certificado por enquanto; os demais centros de custo entram
depois só adicionando entradas no secret, sem mudar este módulo).

`consultar_situacao_lote` é o ponto de entrada real (usado por
fiscal/importador.py e, na Fase 2, por fiscal/sefaz_servico.py) — roda as
consultas em paralelo com timeout curto por chamada, porque o proxy do Fly
corta em 60s e a confirmação de importação já processa lotes de até 100
notas (ver memory/fly-proxy-60s-lotes.md).

Ainda não validado contra o SEFAZ de verdade — o parsing da resposta
(`_interpretar_resposta`) é o primeiro ponto a conferir com
`manage.py verificar_cancelamentos_sefaz --dry-run` em homologação.
"""
from __future__ import annotations

import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import requests
from django.conf import settings
from lxml import etree
from requests_pkcs12 import Pkcs12Adapter
from zeep import Client
from zeep.transports import Transport

# certifi (usado por padrão pelo requests) não confia na ICP-Brasil — sem
# isso toda consulta falha com "unable to get local issuer certificate" (a
# SEFAZ-SP usa AC SOLUTI SSL EV G4, cadeia ICP-Brasil, não uma CA pública
# internacional). Ver fiscal/sefaz_certs/README.md pra origem/validação.
_CA_BUNDLE_SEFAZ = str(Path(__file__).parent / "sefaz_certs" / "icp_brasil_sefaz_sp.pem")

logger = logging.getLogger(__name__)

# NFeConsultaProtocolo4 (NT 2016.002, versão 4.00) — só SP por enquanto (Fase
# 1 do rollout, ver plano). Outro estado entra aqui quando o certificado do
# centro de custo correspondente for cadastrado; este módulo não muda pra
# isso, só o mapa.
_ENDPOINTS_POR_UF = {
    "SP": {
        "homologacao": "https://homologacao.nfe.fazenda.sp.gov.br/ws/nfeconsultaprotocolo4.asmx",
        "producao": "https://nfe.fazenda.sp.gov.br/ws/nfeconsultaprotocolo4.asmx",
    },
}

_NFE_NS = "http://www.portalfiscal.inf.br/nfe"
_NS = {"nfe": _NFE_NS}
# 101 = cancelamento homologado; 151 = cancelamento por SCAN (contingência
# fora de prazo) — confirmar se há variação regional ao validar (ver plano).
_CSTATS_CANCELADA = {"101", "151"}
# 100/150 = autorizado (mesmo conjunto de fiscal/nfe_xml.py::CSTATS_AUTORIZADA,
# mas vindo da SEFAZ agora, não do XML). Qualquer outro cStat (217 = não
# consta na base, 236 = chave rejeitada por dígito inválido, etc.) vira
# NAO_VERIFICADA — nunca AUTORIZADA por default: "não é cancelamento" não
# é o mesmo que "SEFAZ confirmou que está autorizada" (confirmado rodando
# contra homologação de verdade: uma chave malformada voltava cStat=236, e
# a versão anterior deste código classificava isso como AUTORIZADA, errado).
_CSTATS_AUTORIZADA = {"100", "150"}


class Situacao:
    AUTORIZADA = "AUTORIZADA"
    CANCELADA = "CANCELADA"
    NAO_VERIFICADA = "NAO_VERIFICADA"


@dataclass
class ResultadoConsultaSefaz:
    situacao: str  # ver Situacao acima — nunca outro valor
    cstat: str = ""
    xmotivo: str = ""
    protocolo: str = ""
    dh_recbto: datetime | None = None
    # Só preenchido quando situacao == NAO_VERIFICADA — motivo (sem
    # certificado, timeout, erro de rede, resposta inesperada). Nunca uma
    # falha vira CANCELADA por engano.
    erro: str = ""

    @property
    def cancelada(self) -> bool:
        return self.situacao == Situacao.CANCELADA


@lru_cache(maxsize=1)
def _certificados() -> dict[str, tuple[bytes, str]]:
    """CNPJ -> (bytes do .pfx, senha), decodificado uma vez a partir do
    secret FISCAL_SEFAZ_CERTIFICADOS_JSON. Cacheado pro processo inteiro —
    settings não muda em runtime (o processo reinicia quando o secret muda
    no Fly)."""
    bruto = settings.FISCAL_SEFAZ_CERTIFICADOS_JSON
    if not bruto:
        return {}
    dados = json.loads(base64.b64decode(bruto))
    return {
        cnpj: (base64.b64decode(info["pfx_b64"]), info["senha"])
        for cnpj, info in dados.items()
    }


def montar_sessao_soap(cnpj_zanattex: str) -> requests.Session | None:
    """Sessão autenticada com o certificado A1 desse CNPJ, ou None se não
    houver certificado cadastrado — nunca levanta erro; quem chama trata
    isso como NAO_VERIFICADA (ver consultar_situacao)."""
    certificado = _certificados().get(cnpj_zanattex)
    if certificado is None:
        return None
    pfx_bytes, senha = certificado
    sessao = requests.Session()
    sessao.verify = _CA_BUNDLE_SEFAZ
    sessao.mount("https://", Pkcs12Adapter(pkcs12_data=pfx_bytes, pkcs12_password=senha))
    return sessao


# Cliente zeep já é caro de montar (busca + parseia o WSDL) — cacheado por
# (UF, ambiente, CNPJ) já que cada CNPJ usa uma sessão/certificado próprios.
# Com 1 certificado só (Fase 1), isso vira 1 cliente cacheado; cresce sozinho
# conforme mais centros de custo entrarem.
_clientes_soap: dict[tuple[str, str, str], Client] = {}


def _cliente_soap(uf: str, ambiente: str, cnpj_zanattex: str, sessao: requests.Session) -> Client:
    chave = (uf, ambiente, cnpj_zanattex)
    cliente = _clientes_soap.get(chave)
    if cliente is None:
        endpoints = _ENDPOINTS_POR_UF.get(uf)
        if endpoints is None:
            raise ValueError(f'UF "{uf}" sem endpoint de NFeConsultaProtocolo cadastrado em fiscal/sefaz.py.')
        transport = Transport(session=sessao, operation_timeout=settings.FISCAL_SEFAZ_TIMEOUT_SEGUNDOS)
        cliente = Client(endpoints[ambiente] + "?wsdl", transport=transport)
        _clientes_soap[chave] = cliente
    return cliente


def _xml_consulta(chave_acesso: str, ambiente: str) -> etree._Element:
    tp_amb = "1" if ambiente == "producao" else "2"
    xml = (
        f'<consSitNFe xmlns="{_NFE_NS}" versao="4.00">'
        f"<tpAmb>{tp_amb}</tpAmb>"
        f"<xServ>CONSULTAR</xServ>"
        f"<chNFe>{chave_acesso}</chNFe>"
        f"</consSitNFe>"
    )
    return etree.fromstring(xml.encode("utf-8"))


def _texto(no: etree._Element | None, caminho: str) -> str:
    if no is None:
        return ""
    valor = no.findtext(caminho, namespaces=_NS)
    return valor.strip() if valor else ""


def _parse_dh(valor: str) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        return None


def _dados_protocolo_autorizacao(no: etree._Element) -> tuple[str, datetime | None]:
    """protNFe/infProt — o protocolo de quando a nota nasceu (autorizada).
    Só faz sentido usar isso quando a situação ATUAL é AUTORIZADA — ele
    nunca muda depois que a nota é cancelada (ver docstring de
    _interpretar_resposta, é o bug que a auditoria de 2026-09-23 achou)."""
    prot = no.find("nfe:protNFe/nfe:infProt", namespaces=_NS)
    if prot is None:
        return "", None
    return _texto(prot, "nfe:nProt"), _parse_dh(_texto(prot, "nfe:dhRecbto"))


def _dados_cancelamento(no: etree._Element) -> tuple[str, datetime | None]:
    """Protocolo/data do CANCELAMENTO em si — nunca o de autorização
    original (protNFe). Dois modelos, tentados em ordem:
    - `retCancNFe/infCanc` — cancelamento "direto" (modelo pré-2012, ainda
      aparece na resposta mesmo quando o cancelamento de fato foi feito por
      evento, ver abaixo).
    - `procEventoNFe/retEvento/infEvento` com `tpEvento=110111` — evento de
      Cancelamento (modelo atual desde a NT 2011/002); é o que carrega a
      justificativa (`detEvento/xJust`) quando existe."""
    canc = no.find("nfe:retCancNFe/nfe:infCanc", namespaces=_NS)
    if canc is not None:
        protocolo = _texto(canc, "nfe:nProt")
        if protocolo:
            return protocolo, _parse_dh(_texto(canc, "nfe:dhRecbto"))
    for evento in no.findall("nfe:procEventoNFe/nfe:retEvento/nfe:infEvento", namespaces=_NS):
        if _texto(evento, "nfe:tpEvento") == "110111":
            return _texto(evento, "nfe:nProt"), _parse_dh(_texto(evento, "nfe:dhRegEvento"))
    return "", None


def _interpretar_resposta(resposta) -> ResultadoConsultaSefaz:
    """`resposta` é o retorno cru do zeep pra nfeResultMsg (xsd:any) — na
    prática, ou já é um Element lxml (<retConsSitNFe>...), ou um objeto zeep
    que embrulha o Element (varia por versão de zeep/WSDL); os dois casos
    são tratados aqui.

    O `cStat`/`xMotivo` que valem são os do TOPO de `retConsSitNFe` — é
    literalmente a "situação atual" que a consulta existe pra responder.
    `protNFe/infProt` é só o registro de quando a nota nasceu (autorizada) e
    NUNCA muda depois de cancelada — confirmado rodando contra uma NF real
    cancelada em produção (2026-09-23): o topo já vinha `cStat=101`
    (cancelada) enquanto `protNFe` continuava com o `cStat=100` original. A
    versão anterior deste código pegava o `cStat` de dentro de `protNFe` e
    sobrescrevia o do topo — dava AUTORIZADA numa nota que a SEFAZ já tinha
    cancelado meses antes. Nunca mais ler `cStat`/`xMotivo` de dentro de
    `protNFe`."""
    no = resposta
    if hasattr(no, "_value_1"):
        no = no._value_1
    if isinstance(no, list):
        no = no[0] if no else None
    if no is None:
        return ResultadoConsultaSefaz(Situacao.NAO_VERIFICADA, erro="Resposta vazia da SEFAZ.")

    cstat = _texto(no, "nfe:cStat")
    xmotivo = _texto(no, "nfe:xMotivo")

    if cstat in _CSTATS_CANCELADA:
        protocolo, dh_recbto = _dados_cancelamento(no)
        return ResultadoConsultaSefaz(
            Situacao.CANCELADA, cstat=cstat, xmotivo=xmotivo, protocolo=protocolo, dh_recbto=dh_recbto)
    if cstat in _CSTATS_AUTORIZADA:
        protocolo, dh_recbto = _dados_protocolo_autorizacao(no)
        return ResultadoConsultaSefaz(
            Situacao.AUTORIZADA, cstat=cstat, xmotivo=xmotivo, protocolo=protocolo, dh_recbto=dh_recbto)

    # Qualquer outro cStat (não localizada, rejeitada, denegada...) — não é
    # fato confirmado de cancelamento nem de autorização, fica como não
    # verificada, com o motivo da SEFAZ no erro pra quem for investigar (ver
    # docstring de _CSTATS_AUTORIZADA).
    if not cstat:
        erro = "Resposta da SEFAZ sem cStat reconhecível."
    else:
        erro = f"SEFAZ respondeu cStat={cstat} ({xmotivo or 'sem motivo informado'}) — nem autorizada nem cancelada."
    return ResultadoConsultaSefaz(Situacao.NAO_VERIFICADA, cstat=cstat, xmotivo=xmotivo, erro=erro)


def consultar_situacao(chave_acesso: str, cnpj_zanattex: str) -> ResultadoConsultaSefaz:
    """Só leitura — nunca grava nada. Nunca levanta exceção: qualquer falha
    (sem UF cadastrada, sem certificado, timeout, erro de rede, resposta
    inesperada) vira NAO_VERIFICADA — quem chama decide o que fazer (ver
    fiscal/importador.py e, na Fase 2, fiscal/sefaz_servico.py)."""
    uf = settings.FISCAL_SEFAZ_UF_POR_CNPJ.get(cnpj_zanattex)
    if not uf:
        return ResultadoConsultaSefaz(
            Situacao.NAO_VERIFICADA, erro=f'CNPJ "{cnpj_zanattex}" sem UF cadastrada em FISCAL_SEFAZ_UF_POR_CNPJ.')

    sessao = montar_sessao_soap(cnpj_zanattex)
    if sessao is None:
        return ResultadoConsultaSefaz(
            Situacao.NAO_VERIFICADA, erro=f'Sem certificado cadastrado pro CNPJ "{cnpj_zanattex}".')

    ambiente = settings.FISCAL_SEFAZ_AMBIENTE
    try:
        cliente = _cliente_soap(uf, ambiente, cnpj_zanattex, sessao)
        # O parâmetro da operação é um xsd:any anônimo — zeep expõe como
        # posicional (`_value_1`), não como o nome "nfeDadosMsg" da
        # documentação da NF-e (confirmado rodando contra a SEFAZ de
        # homologação de verdade: passar por keyword dava TypeError).
        resposta = cliente.service.nfeConsultaNF(_xml_consulta(chave_acesso, ambiente))
    except Exception as e:
        logger.warning("Falha ao consultar SEFAZ pra chave %s (CNPJ %s): %s", chave_acesso, cnpj_zanattex, e)
        return ResultadoConsultaSefaz(Situacao.NAO_VERIFICADA, erro=str(e))

    return _interpretar_resposta(resposta)


def consultar_situacao_lote(pares: list[tuple[str, str]]) -> dict[str, ResultadoConsultaSefaz]:
    """`pares`: [(chave_acesso, cnpj_zanattex), ...]. Roda em paralelo
    (até FISCAL_SEFAZ_MAX_PARALELO workers) — é o que mantém o tempo do lote
    de importação previsível mesmo com dezenas/centenas de notas (ver
    memory/fly-proxy-60s-lotes.md). Uma chave lenta nunca segura as outras:
    o `operation_timeout` por chamada (FISCAL_SEFAZ_TIMEOUT_SEGUNDOS) já
    garante isso dentro de cada thread."""
    if not pares:
        return {}
    resultado: dict[str, ResultadoConsultaSefaz] = {}
    max_workers = min(settings.FISCAL_SEFAZ_MAX_PARALELO, len(pares))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futuros = {executor.submit(consultar_situacao, chave, cnpj): chave for chave, cnpj in pares}
        for futuro in as_completed(futuros):
            chave = futuros[futuro]
            try:
                resultado[chave] = futuro.result()
            except Exception as e:  # nunca deixa uma falha inesperada derrubar o lote inteiro
                logger.exception("Falha inesperada consultando SEFAZ pra chave %s", chave)
                resultado[chave] = ResultadoConsultaSefaz(Situacao.NAO_VERIFICADA, erro=str(e))
    return resultado
