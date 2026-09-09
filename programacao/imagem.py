"""Versão em imagem (PNG) do relatório de Programação de Corte — mesma
estrutura visual do PDF (programacao/relatorio_pdf.py), mesmos dados, mas
pra abrir direto na conversa do grupo do PCP sem precisar baixar/abrir
anexo. Usa Pillow puro (sem navegador headless) — `ImageFont.load_default`
com `size` é resolvido internamente pelo Pillow (funciona igual em
Windows/Linux, sem depender de fonte instalada no servidor)."""
from __future__ import annotations

import io
import os

from PIL import Image, ImageDraw, ImageFont

# Fonte variável (Regular/Bold no mesmo arquivo, via eixo de peso) — mesma
# família (Inter) usada no resto do app (static/css/app.css). Baixada de
# google/fonts (OFL) em static/fonts/ — sem isso, o bitmap font embutido do
# Pillow (ImageFont.load_default) não cobre acentuação (ç, ã, é...).
_FONTE_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "fonts", "Inter-Variable.ttf")

# Mesma paleta de static/css/app.css e producao/relatorio_pdf.py
NAVY = (23, 37, 84)
RED = (220, 38, 38)
BORDER = (226, 232, 240)
INK = (30, 41, 59)
FAINT = (107, 114, 128)
ZEBRA = (248, 250, 252)
WHITE = (255, 255, 255)

# A largura sai do texto real (ver `_larguras_colunas`): LARGURA_MIN é o piso,
# pra semana com poucas OPs não virar um card estreito, e LARGURA_MAX o teto,
# porque acima disso o PNG fica ilegível no celular — que é onde o pessoal do
# PCP abre. Batendo no teto, o texto sai com reticências em vez de vazar.
LARGURA_MIN = 900
LARGURA_MAX = 1500
PADDING = 26
PAD_COL = 20  # respiro entre colunas
LINHA_ALTURA = 30
COLUNA_FLEX = 3  # Produto absorve a sobra, igual ao PDF (_larguras_auto)


def _fonte(tamanho: int, negrito: bool = False) -> ImageFont.FreeTypeFont:
    try:
        fonte = ImageFont.truetype(_FONTE_PATH, tamanho)
        fonte.set_variation_by_name(b"Bold" if negrito else b"Regular")
        return fonte
    except OSError:
        # Fallback se o arquivo de fonte não estiver disponível (ex.: ambiente
        # sem static/fonts/ copiado) — pior legibilidade em acentos, mas não quebra.
        return ImageFont.load_default(size=tamanho)


def gerar_imagem_programacao(*, semana_label: str, periodo_label: str, gerado_em: str,
                             grupos: list[dict], local_label: str | None = None) -> bytes:
    """Mesma assinatura de dados de `relatorio_pdf.gerar_pdf_programacao` —
    `grupos`: [{"local_label", "itens": [{"pedido","cliente","unidade",
    "produto","qtde","destino","previsao"}]}]. Retorna bytes do PNG."""
    f_wm = _fonte(22, negrito=True)
    f_tag = _fonte(11)
    f_tit = _fonte(15, negrito=True)
    f_sub = _fonte(11)
    f_sub_navy = _fonte(12, negrito=True)
    f_th = _fonte(11, negrito=True)
    f_cell = _fonte(11)
    f_rodape = _fonte(10)

    cabecalho = ["Pedido", "Cliente", "Unidade", "Produto", "Qtde", "Destino", "Previsão"]
    linhas_todas = [
        [item["pedido"], item["cliente"], item["unidade"], item["produto"],
         _fmt(item["qtde"]), item["destino"], item["previsao"]]
        for g in grupos for item in g["itens"]
    ]
    largura, col_w = _larguras_colunas(cabecalho, linhas_todas, f_th, f_cell)
    col_x = _posicoes(col_w)

    # Por grupo: 34px da faixa navy do cabeçalho + 22px da linha de títulos
    # de coluna + 30px por item + 10px de respiro depois — bater exatamente
    # com o que o loop abaixo desenha (y += 34; y += 22; y += LINHA_ALTURA
    # por item; y += 10), senão a altura da imagem fica menor que o
    # conteúdo de verdade e o rodapé desenha por cima da última tabela.
    n_grupos = len(grupos) or 1
    total_itens = sum(len(g["itens"]) for g in grupos)
    altura = 150 + n_grupos * 66 + total_itens * LINHA_ALTURA + 60
    img = Image.new("RGB", (largura, altura), WHITE)
    d = ImageDraw.Draw(img)

    # Faixa de marca
    d.rectangle([0, 0, largura, 4], fill=RED)
    d.rectangle([0, 4, largura, 96], fill=NAVY)
    _wordmark_zanattex(d, PADDING, 20, f_wm)
    d.text((PADDING, 48), "CENTRAL DE DADOS", font=f_tag, fill=(203, 213, 225))

    titulo = f"Programação de Corte — {local_label}" if local_label else "Programação de Corte"
    _texto_dir(d, titulo, largura - PADDING, 18, f_tit, WHITE)
    _texto_dir(d, f"Período: {periodo_label}", largura - PADDING, 42, f_sub, (203, 213, 225))
    _texto_dir(d, f"Gerado em {gerado_em}", largura - PADDING, 62, f_sub, (203, 213, 225))

    y = 116
    total_ops = sum(len(g["itens"]) for g in grupos)
    total_pecas = sum(item["qtde"] for g in grupos for item in g["itens"])
    d.text((PADDING, y), f"{total_ops} OPs · {_fmt(total_pecas)} peças programadas", font=f_sub_navy, fill=NAVY)
    y += 34

    for grupo in grupos:
        soma = sum(item["qtde"] for item in grupo["itens"])
        d.rectangle([PADDING - 10, y, largura - PADDING + 10, y + 26], fill=NAVY)
        d.text((PADDING, y + 5), f"{grupo['local_label'].upper()} — {_fmt(soma)} pçs ({len(grupo['itens'])} OPs)",
               font=f_sub_navy, fill=WHITE)
        y += 34

        for i, texto in enumerate(cabecalho):
            _celula(d, texto, col_x[i], col_w[i], y, f_th, FAINT, direita=(i == 4))
        y += 22
        for i, item in enumerate(grupo["itens"]):
            if i % 2 == 1:
                d.rectangle([PADDING - 10, y - 2, largura - PADDING + 10, y + 22], fill=ZEBRA)
            valores = [item["pedido"], item["cliente"], item["unidade"], item["produto"],
                      _fmt(item["qtde"]), item["destino"], item["previsao"]]
            for j, texto in enumerate(valores):
                _celula(d, str(texto), col_x[j], col_w[j], y, f_cell, INK, direita=(j == 4))
            y += LINHA_ALTURA
        y += 10

    d.line([(PADDING, altura - 34), (largura - PADDING, altura - 34)], fill=BORDER)
    d.text((PADDING, altura - 26), "Zanattex Indústria · Arealva/SP", font=f_rodape, fill=FAINT)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Layout das colunas ───────────────────────────────────────────────────────
def _larguras_colunas(cabecalho: list[str], linhas: list[list], f_th, f_cell) -> tuple[int, list[int]]:
    """Cada coluna recebe a largura do seu conteúdo mais largo (cabeçalho ou
    valor, medidos na fonte real) + respiro, e a imagem cresce até caber tudo.
    Mesmo critério do PDF (relatorio_pdf.py::_larguras_auto): sobrando espaço
    quem cresce é a coluna Produto; faltando, é ela quem cede primeiro, e só
    depois o resto encolhe proporcional. Antes as posições eram fixas e o
    texto longo (Destino) invadia a coluna vizinha e vazava da imagem."""
    minimos = []
    for i, cab in enumerate(cabecalho):
        largura = f_th.getlength(cab)
        for linha in linhas:
            largura = max(largura, f_cell.getlength(str(linha[i])))
        minimos.append(int(largura) + PAD_COL)

    util = sum(minimos)
    largura_img = min(max(LARGURA_MIN, util + 2 * PADDING), LARGURA_MAX)
    disponivel = largura_img - 2 * PADDING

    larguras = list(minimos)
    # Sobra positiva alarga o Produto; negativa (estourou LARGURA_MAX) encolhe
    # ele até um piso legível, e o que ainda faltar sai no rateio abaixo.
    larguras[COLUNA_FLEX] = max(90, larguras[COLUNA_FLEX] + (disponivel - util))

    if sum(larguras) > disponivel:
        escala = disponivel / sum(larguras)
        larguras = [max(40, int(w * escala)) for w in larguras]
    return largura_img, larguras


def _posicoes(larguras: list[int]) -> list[int]:
    x, xs = PADDING, []
    for w in larguras:
        xs.append(x)
        x += w
    return xs


def _celula(draw: ImageDraw.ImageDraw, texto: str, x: int, w: int, y: int,
            font, fill, direita: bool = False) -> None:
    """Desenha o texto preso à sua coluna — o que não couber vira reticências,
    então nunca sobrepõe a coluna seguinte nem sai da imagem. `direita` alinha
    à direita (Qtde), igual ao PDF (aligns=["l","l","l","r","l","l"])."""
    max_w = max(10, w - PAD_COL)
    texto = _elidir(texto, font, max_w)
    if direita:
        _texto_dir(draw, texto, x + w - PAD_COL // 2, y, font, fill)
    else:
        draw.text((x, y), texto, font=font, fill=fill)


def _elidir(texto: str, font, max_w: float) -> str:
    if font.getlength(texto) <= max_w:
        return texto
    reticencias = "…"
    w_ret = font.getlength(reticencias)
    corte = texto
    while corte and font.getlength(corte) + w_ret > max_w:
        corte = corte[:-1]
    return corte.rstrip() + reticencias if corte else ""


def _wordmark_zanattex(draw: ImageDraw.ImageDraw, x: int, y: int, font) -> None:
    """"ZANATTEX" com Z e X em vermelho, igual à faixa dos outros relatórios
    (producao/relatorio_pdf.py::_faixa_marca)."""
    partes = [("Z", RED), ("ANATTE", WHITE), ("X", RED)]
    for texto, cor in partes:
        draw.text((x, y), texto, font=font, fill=cor)
        x += draw.textlength(texto, font=font)


def _texto_dir(draw: ImageDraw.ImageDraw, texto: str, x_direita: int, y: int, font, fill) -> None:
    largura = draw.textlength(texto, font=font)
    draw.text((x_direita - largura, y), texto, font=font, fill=fill)


def _fmt(v) -> str:
    try:
        return f"{int(v):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(v)
