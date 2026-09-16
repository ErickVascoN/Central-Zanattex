from django.conf import settings
from django.test import SimpleTestCase, TestCase

# Create your tests here.


class ComentarioDeTemplateTests(SimpleTestCase):
    """`{# ... #}` do Django é de UMA linha só. Espalhado em várias, ele
    deixa de ser reconhecido como comentário e o texto vaza pra tela como
    conteúdo — sem erro, sem aviso, só um parágrafo estranho no meio da
    página. Já aconteceu três vezes neste projeto (prestador_lista.html,
    o Balanço em detalhe.html e o seletor de arranjo da ficha da OP), o
    que é sinal de que revisar no olho não basta. Para comentário de mais
    de uma linha o certo é {% comment %}...{% endcomment %}."""

    def test_nenhum_comentario_de_template_ocupa_mais_de_uma_linha(self):
        import re
        from pathlib import Path

        raiz = Path(settings.BASE_DIR) / "templates"
        padrao = re.compile(r"\{#.*?#\}", re.DOTALL)
        vazando = []
        for arquivo in raiz.rglob("*.html"):
            texto = arquivo.read_text(encoding="utf-8")
            for achado in padrao.finditer(texto):
                if "\n" in achado.group():
                    linha = texto[: achado.start()].count("\n") + 1
                    vazando.append(f"{arquivo.relative_to(raiz)}:{linha}")

        self.assertEqual(
            vazando, [],
            "Comentário {# #} ocupando mais de uma linha — vira texto visível "
            "na tela. Troque por {% comment %}...{% endcomment %} em: "
            + ", ".join(vazando))
