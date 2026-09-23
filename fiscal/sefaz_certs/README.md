# Cadeia de CA da SEFAZ-SP (ICP-Brasil)

`icp_brasil_sefaz_sp.pem` = certificado raiz `ICP-Brasil` (Autoridade
Certificadora Raiz Brasileira v10) + intermediária `AC SOLUTI SSL EV G4`, que
é quem assina o certificado TLS de `*.nfe.fazenda.sp.gov.br` (homologação e
produção — confirmado nos dois, 2026-09-23).

Sem isso, `requests`/`certifi` recusa a conexão com
`SSLError: [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer
certificate` — o certifi (usado por padrão pelo `requests`) só confia em CAs
públicas reconhecidas internacionalmente; a ICP-Brasil é a raiz da
infraestrutura de chaves públicas do governo brasileiro, não faz parte
desse conjunto.

Baixado do repositório oficial do ITI (Instituto Nacional de Tecnologia da
Informação, o órgão que administra a ICP-Brasil):
`https://acraiz.icpbrasil.gov.br/credenciadas/CertificadosAC-ICP-Brasil/ACcompactado.zip`
— arquivos `ICP-Brasilv10.crt` + `AC-SOLUTI-SSL-EV-G4.crt`, concatenados.

Validado com `openssl s_client -CAfile icp_brasil_sefaz_sp.pem -connect
<host>:443` contra homologação e produção — `Verify return code: 0 (ok)`
nos dois.

**Quando reconstruir:** o certificado da intermediária (AC SOLUTI SSL EV G4)
vale até 2032 — só precisa mexer aqui se a SEFAZ-SP trocar de autoridade
certificadora antes disso, ou se `fiscal/sefaz.py` passar a falar com outra
UF que use uma cadeia diferente (baixar o `.zip` de novo do link acima e
trocar a intermediária certa pro novo host).
