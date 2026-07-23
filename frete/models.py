"""Modelos da Calculadora de Frete — persistência no banco do app (substitui o
Supabase). Espelham o schema antigo (clients / freight_calculations)."""

from django.db import models


class Cliente(models.Model):
    nome = models.CharField("Nome", max_length=200, unique=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cliente (frete)"
        verbose_name_plural = "Clientes (frete)"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class CalculoFrete(models.Model):
    cliente = models.ForeignKey(
        Cliente, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="calculos",
    )
    data = models.DateField("Data do cálculo")
    destino = models.CharField(max_length=300, blank=True, default="")
    distancia_km = models.FloatField(null=True, blank=True)
    ida_volta = models.BooleanField(default=False)
    veiculo = models.CharField(max_length=200, blank=True, default="")
    cavalo = models.CharField(max_length=200, blank=True, default="")

    # componentes de custo
    diesel = models.FloatField(default=0)
    motorista = models.FloatField(default=0)
    pedagio = models.FloatField(default=0)
    manutencao = models.FloatField(default=0)
    arla = models.FloatField(default=0)
    depreciacao = models.FloatField(default=0)
    seguro = models.FloatField(default=0)
    gris = models.FloatField(default=0)
    multa = models.FloatField(default=0)

    valor_carga = models.FloatField(default=0)
    risco_pct = models.FloatField(default=0)
    impostos_pct = models.FloatField(default=0)
    impostos_valor = models.FloatField(default=0)
    margem_pct = models.FloatField(default=0)
    valor_frete = models.FloatField(default=0)

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cálculo de frete"
        verbose_name_plural = "Cálculos de frete"
        ordering = ["-data", "-criado_em"]

    def __str__(self):
        return f"{self.destino or '—'} · {self.data}"

    # mapeamento coluna do modelo -> chave usada no JS/analytics da calculadora
    _JS_MAP = {
        "diesel": "diesel", "motorista": "driver", "pedagio": "toll",
        "manutencao": "maintenance", "arla": "arla", "depreciacao": "depreciation",
        "seguro": "insurance", "gris": "gris_total", "multa": "fine",
    }

    def para_js(self):
        """Formato consumido pelo JS da calculadora (mesmas chaves do Supabase)."""
        d = {
            "id": self.id,
            "client_id": self.cliente_id,
            "calc_date": self.data.isoformat(),
            "destination": self.destino or None,
            "distance_km": self.distancia_km,
            "round_trip": self.ida_volta,
            "vehicle_id": self.veiculo or None,
            "tractor_id": self.cavalo or None,
            "cargo_value": self.valor_carga,
            "risk_pct": self.risco_pct,
            "taxes_pct": self.impostos_pct,
            "taxes_value": self.impostos_valor,
            "profit_pct": self.margem_pct,
            "total_freight": self.valor_frete,
        }
        for campo, js in self._JS_MAP.items():
            d[js] = getattr(self, campo)
        return d
