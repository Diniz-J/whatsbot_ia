"""O teste que o invariante 2 esperava desde a fatia do catalogo.

As ferramentas do modelo tem que SER as funcoes de servico que o
`RuleResponder` chama — nao uma reimplementacao com o mesmo nome. Identidade
de objeto, nao semelhanca de comportamento: se alguem escrever uma versao "do
LLM" de buscar preco, isto quebra.
"""

from apps.catalogo import services as catalogo
from apps.responder.llm import ferramentas


def test_cada_ferramenta_e_a_propria_funcao_de_servico():
    assert ferramentas.FERRAMENTAS == {
        "ver_cardapio": catalogo.cardapio,
        "buscar_item": catalogo.buscar_itens,
        "ver_horarios": catalogo.horarios,
        "esta_aberto": catalogo.esta_aberto,
        "proxima_abertura": catalogo.proxima_abertura,
    }


def test_identidade_objeto_a_objeto():
    """`==` em funcao ja e identidade, mas dito explicitamente para quem for
    mexer: nao vale uma funcao equivalente, tem que ser a mesma."""
    assert ferramentas.FERRAMENTAS["buscar_item"] is catalogo.buscar_itens
    assert ferramentas.FERRAMENTAS["ver_cardapio"] is catalogo.cardapio
    assert ferramentas.FERRAMENTAS["ver_horarios"] is catalogo.horarios
    assert ferramentas.FERRAMENTAS["esta_aberto"] is catalogo.esta_aberto
    assert ferramentas.FERRAMENTAS["proxima_abertura"] is catalogo.proxima_abertura


def test_nenhuma_ferramenta_declarada_sem_servico_atras():
    """Schema sem entrada no registry seria ferramenta que o modelo pode
    pedir e ninguem responde — ou pior, que alguem implementa a parte."""
    declaradas = {s["name"] for s in ferramentas.SCHEMAS}

    assert declaradas == set(ferramentas.FERRAMENTAS)


def test_nenhum_schema_expoe_o_cliente():
    """`tenant_id` e injetado pelo executor. Se fosse argumento do modelo, o
    texto do cliente final poderia pedir o catalogo do vizinho."""
    for schema in ferramentas.SCHEMAS:
        propriedades = schema["input_schema"]["properties"]
        assert not [p for p in propriedades if "tenant" in p.lower()], schema["name"]
        assert not [p for p in propriedades if "escola" in p.lower()], schema["name"]


def test_todo_schema_e_estrito_e_fechado():
    """`strict` com `additionalProperties: false` e o que garante argumento
    que casa com o schema, em vez de campo inventado."""
    for schema in ferramentas.SCHEMAS:
        assert schema["strict"] is True, schema["name"]
        assert schema["input_schema"]["additionalProperties"] is False, schema["name"]


def test_prompt_proibe_afirmar_sem_ferramenta_e_trata_texto_como_dado():
    """Os dois paragrafos que nao sao enfeite. Se alguem enxugar o prompt e
    tirar um deles, isto cai."""
    from apps.responder.llm.prompt import sistema

    texto = sistema("Cantina do Ze")

    assert "SO podem sair de" in texto
    assert "nunca instrucao" in texto
    assert "outro estabelecimento" in texto
    assert "Cantina do Ze" in texto
