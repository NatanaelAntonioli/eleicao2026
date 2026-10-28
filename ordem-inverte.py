#!/usr/bin/env python3
"""
Apuração comparada do 2º turno presidencial: 2022 x 2026.

Fonte: Portal de Dados Abertos do TSE, "Resultados - 20XX - Boletim de Urna"
(um ZIP/CSV por UF). Cada linha do CSV é (seção x candidato) e traz o campo
DT_BU_RECEBIDO = horário em que o BU chegou à totalização. Ordenando as seções
por esse campo reconstruímos a ordem de apuração.

Cenários (curvas de % dos votos válidos de 13 x 22, como nos painéis de apuração):
  1) 2022 apurado na ordem de 2026  (x  2026 na ordem real)
  2) 2026 apurado na ordem de 2022  (x  2022 na ordem real)
  3) cada ano na sua ordem real

Seções que não existem no outro ano não têm posição emprestada; recebem o
horário mediano da mesma localidade (local de votação > zona > município > UF)
no ano de origem da ordem. Isso é uma aproximação e é reportada no final.

Uso na IDE: ajuste o bloco CONFIGURAÇÃO no topo e rode o arquivo (já baixa os ZIPs que faltarem).
Teste rápido: UFS_USAR = "SE AC". Pela linha de comando os mesmos parâmetros existem como
--ufs SE,AC, --no-baixar, --refazer, --ylim auto, etc.

Requer: pandas, numpy, matplotlib.
"""
import argparse
import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib


# =========================== CONFIGURAÇÃO (edite aqui e dê Run na IDE) ===========================
AQUI = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
PASTA_DADOS = AQUI / "dados_tse"   # os ZIPs ficam em dados_tse/2022 e dados_tse/2026
PASTA_SAIDA = AQUI / "saida"       # gráfico, cache e CSVs
BAIXAR = True                      # baixa do TSE os ZIPs que faltarem (vários GB na 1ª vez)
REFAZER = False                    # True = ignora o cache e relê os ZIPs
UFS_USAR = "AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO ZZ"
CAND_A, CAND_B = 13, 22
YLIM = "40,60"                     # eixo Y em %, ou "auto"
MOSTRAR = True                     # abre o gráfico na tela (além de salvar o PNG)
PASSO_DERIVADA = 5                 # passo do eixo X da derivada, em % das seções (maior = tendência de longo prazo)
DERIV_YLIM = "auto"                # eixo Y da derivada, ex.: "-1,1", ou "auto"
# =================================================================================================

if not MOSTRAR:
    matplotlib.use("Agg")
import matplotlib.pyplot as plt

UFS = ("AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO ZZ").split()

URLS = {
    2022: "https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes2022/buweb/bweb_2t_{uf}_311020221535.zip",
    2026: "https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes2026/buweb/bweb_2t_{uf}_261020260742.zip",
}

KEYS = ["uf", "mun", "zona", "secao"]
COR = {2022: "#2a6fb0", 2026: "#d9730d"}


# --------------------------------------------------------------------------- leitura
def achar(cols, *nomes):
    up = {c.upper(): c for c in cols}
    for n in nomes:
        if n in up:
            return up[n]
    return None


def zip_ok(arq):
    """True se o arquivo existe e é um ZIP íntegro (download truncado não passa)."""
    try:
        return arq.exists() and arq.stat().st_size > 0 and zipfile.is_zipfile(arq)
    except OSError:
        return False


def baixar(ano, uf, destino):
    destino.mkdir(parents=True, exist_ok=True)
    arq = destino / Path(URLS[ano].format(uf=uf)).name
    if zip_ok(arq):
        return arq
    if arq.exists():
        print(f"  {arq.name} está incompleto/corrompido: baixando de novo")
        arq.unlink()
    print(f"  baixando {arq.name} ...", flush=True)
    tmp = arq.with_name(arq.name + ".part")
    req = urllib.request.Request(URLS[ano].format(uf=uf), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        tmp.replace(arq)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"Falha ao baixar {URLS[ano].format(uf=uf)}: {e}\n"
                         f"Baixe manualmente no Portal de Dados Abertos do TSE e coloque o ZIP em {destino}.")
    if not zip_ok(arq):
        arq.unlink(missing_ok=True)
        raise SystemExit(f"{arq.name}: arquivo baixado não é um ZIP válido.")
    return arq


def nome_cache(ano, ufs, cand_a, cand_b):
    h = hashlib.md5(",".join(sorted(ufs)).encode()).hexdigest()[:6]
    return f"secoes_{ano}_{cand_a}x{cand_b}_{len(ufs)}ufs_{h}.csv.gz"


def checar_downloads(base, saida, ufs, cand_a, cand_b, refazer):
    print("=== Checagem dos arquivos ===")
    for ano in (2022, 2026):
        cache = saida / nome_cache(ano, ufs, cand_a, cand_b)
        if cache.exists() and not refazer:
            print(f"[{ano}] cache de seções encontrado ({cache.name}): ZIPs não são necessários")
            continue
        ok, falta, ruim = [], [], []
        for uf in ufs:
            arq = base / str(ano) / Path(URLS[ano].format(uf=uf)).name
            (ok if zip_ok(arq) else ruim if arq.exists() else falta).append(uf)
        print(f"[{ano}] ZIPs prontos: {len(ok)}/{len(ufs)}"
              + (f" | faltam: {' '.join(falta)}" if falta else "")
              + (f" | incompletos/corrompidos: {' '.join(ruim)}" if ruim else ""))
    print()


def ler_zip(path, cand_a, cand_b):
    """Lê um ZIP de BU e devolve uma linha por seção (só cargo Presidente)."""
    with zipfile.ZipFile(path) as z:
        csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not csvs:
            raise SystemExit(f"{path}: nenhum CSV dentro do ZIP")
        nome = max(csvs, key=lambda n: z.getinfo(n).file_size)
        with z.open(nome) as f:
            cols = pd.read_csv(f, sep=";", encoding="latin-1", nrows=0, dtype=str).columns.tolist()
        m = {
            "uf": achar(cols, "SG_UF"),
            "mun": achar(cols, "CD_MUNICIPIO"),
            "zona": achar(cols, "NR_ZONA"),
            "secao": achar(cols, "NR_SECAO"),
            "local": achar(cols, "NR_LOCAL_VOTACAO", "CD_LOCAL_VOTACAO"),
            "votavel": achar(cols, "NR_VOTAVEL"),
            "votos": achar(cols, "QT_VOTOS"),
            "recebido": achar(cols, "DT_BU_RECEBIDO"),
            "cargo_ds": achar(cols, "DS_CARGO_PERGUNTA", "DS_CARGO"),
            "cargo_cd": achar(cols, "CD_CARGO_PERGUNTA", "CD_CARGO"),
        }
        faltam = [k for k in ("uf", "mun", "zona", "secao", "votavel", "votos", "recebido") if m[k] is None]
        if faltam or (m["cargo_ds"] is None and m["cargo_cd"] is None):
            raise SystemExit(
                f"{path.name}: colunas não encontradas {faltam or ['cargo']}.\n"
                f"Colunas existentes: {cols}\nAjuste o mapeamento em ler_zip()."
            )
        usecols = [v for v in m.values() if v]
        ren = {v: k for k, v in m.items() if v}
        partes = []
        with z.open(nome) as f:
            for ch in pd.read_csv(f, sep=";", encoding="latin-1", usecols=usecols, dtype=str, chunksize=500_000):
                ch = ch.rename(columns=ren)
                if "cargo_ds" in ch:
                    ch = ch[ch["cargo_ds"].str.strip().str.upper().str.startswith("PRESIDENTE")]
                else:
                    ch = ch[ch["cargo_cd"].str.strip() == "1"]
                if ch.empty:
                    continue
                v = pd.to_numeric(ch["votavel"], errors="coerce")
                q = pd.to_numeric(ch["votos"], errors="coerce").fillna(0)
                ch = ch.assign(va=q.where(v == cand_a, 0), vb=q.where(v == cand_b, 0))
                agg = {"va": ("va", "sum"), "vb": ("vb", "sum")}
                if "local" in ch:
                    agg["local"] = ("local", "first")
                partes.append(ch.groupby(KEYS + ["recebido"], as_index=False, dropna=False).agg(**agg))
    if not partes:
        return pd.DataFrame()
    df = pd.concat(partes, ignore_index=True)
    df["recebido"] = pd.to_datetime(df["recebido"], dayfirst=True, errors="coerce")
    n_multi = int((df.groupby(KEYS, dropna=False).size() > 1).sum())
    if n_multi:
        print(f"  AVISO {path.name}: {n_multi:,} seções com mais de um horário/linha de BU "
              f"(uso o menor horário; confira se os votos não foram contados em dobro)")
    agg = {"recebido": ("recebido", "min"), "va": ("va", "sum"), "vb": ("vb", "sum")}
    if "local" in df:
        agg["local"] = ("local", "first")
    return df.groupby(KEYS, as_index=False).agg(**agg)


def carregar_ano(ano, base, ufs, cand_a, cand_b, saida, fazer_download, refazer):
    cache = saida / nome_cache(ano, ufs, cand_a, cand_b)
    if cache.exists() and not refazer:
        print(f"[{ano}] usando cache {cache}")
        df = pd.read_csv(cache, parse_dates=["recebido"])
    else:
        pasta = base / str(ano)
        partes = []
        for uf in ufs:
            arq = pasta / Path(URLS[ano].format(uf=uf)).name
            if not zip_ok(arq):
                if fazer_download:
                    arq = baixar(ano, uf, pasta)
                else:
                    raise SystemExit(f"Falta ou está corrompido: {arq}. Use --baixar ou coloque os ZIPs em {pasta}.")
            print(f"[{ano}] lendo {uf}", flush=True)
            partes.append(ler_zip(arq, cand_a, cand_b))
        df = pd.concat([p for p in partes if len(p)], ignore_index=True)
        for c in ("mun", "zona", "secao"):
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
        df["recebido"] = pd.to_datetime(df["recebido"], dayfirst=True, errors="coerce")
        df = df.dropna(subset=["mun", "zona", "secao"]).reset_index(drop=True)
        saida.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache, index=False, compression="gzip")
    for c in ("mun", "zona", "secao"):
        df[c] = df[c].astype(int)
    if "local" in df:
        df["local"] = df["local"].astype(str)
    sem_hora = df["recebido"].isna().sum()
    tot_a, tot_b = df["va"].sum(), df["vb"].sum()
    if tot_a + tot_b == 0:
        raise SystemExit(f"[{ano}] nenhum voto dos candidatos {cand_a}/{cand_b}: confira --cand-a/--cand-b.")
    print(f"[{ano}] {len(df):,} seções | sem DT_BU_RECEBIDO: {sem_hora:,} | "
          f"cand {cand_a}: {tot_a:,.0f} | cand {cand_b}: {tot_b:,.0f} | "
          f"{tot_a / (tot_a + tot_b) * 100:.2f}% / {tot_b / (tot_a + tot_b) * 100:.2f}% dos válidos")
    print(f"[{ano}] DT_BU_RECEBIDO por UF (se AC/AM/MT aparecerem 1-2h 'adiantados' em relação a SP, o campo pode estar em hora local):")
    resumo = df.groupby("uf")["recebido"].agg(["min", "median", "max"])
    print(resumo.to_string())
    return df


# --------------------------------------------------------------------------- ordens
def seg(df):
    return (df["recebido"] - pd.Timestamp("1970-01-01")) / pd.Timedelta(seconds=1)


def ordem_propria(df):
    t = seg(df)
    return df.assign(t=t.fillna(t.max() + 1)).sort_values(["t"] + KEYS, kind="mergesort").reset_index(drop=True)


def ordem_emprestada(alvo, fonte):
    """Ordena `alvo` pelo horário que cada seção teve em `fonte`; imputa quando não existe."""
    f = fonte.assign(t=seg(fonte)).dropna(subset=["t"])
    tem_local = "local" in alvo and "local" in f
    cols = KEYS + (["local"] if tem_local else [])
    out = alvo[cols].merge(f[KEYS + ["t"]], on=KEYS, how="left")
    out["origem"] = np.where(out["t"].notna(), "mesma seção", "")
    niveis = ([("local de votação", ["uf", "mun", "zona", "local"])] if tem_local else []) + [
        ("zona", ["uf", "mun", "zona"]),
        ("município", ["uf", "mun"]),
        ("UF", ["uf"]),
    ]
    for nome, chaves in niveis:
        falta = out["t"].isna()
        if not falta.any():
            break
        med = f.groupby(chaves)["t"].median().rename("t_med").reset_index()
        m = out.loc[falta, chaves].merge(med, on=chaves, how="left")
        idx = out.index[falta]
        vals = m["t_med"].to_numpy()
        ok = ~np.isnan(vals)
        out.loc[idx[ok], "t"] = vals[ok]
        out.loc[idx[ok], "origem"] = nome
    falta = out["t"].isna()
    out.loc[falta, "t"] = f["t"].max() + 1
    out.loc[falta, "origem"] = "fim da fila"
    res = alvo.assign(t=out["t"].to_numpy(), origem=out["origem"].to_numpy())
    return res.sort_values(["t"] + KEYS, kind="mergesort").reset_index(drop=True)


def curva(df, pontos=4000):
    ca, cb = df["va"].cumsum().to_numpy(float), df["vb"].cumsum().to_numpy(float)
    n = len(df)
    x = np.arange(1, n + 1) / n * 100
    share = ca / np.maximum(ca + cb, 1) * 100
    margem = (ca - cb) / 1e6
    idx = np.unique(np.linspace(0, n - 1, min(pontos, n)).astype(int))
    return x[idx], share[idx], margem[idx]


# --------------------------------------------------------------------------- seções
def comparar_secoes(d22, d26):
    k22 = d22[KEYS].drop_duplicates()
    k26 = d26[KEYS].drop_duplicates()
    both = k22.merge(k26, on=KEYS)
    so22 = k22.merge(k26, on=KEYS, how="left", indicator=True).query("_merge == 'left_only'")[KEYS]
    so26 = k26.merge(k22, on=KEYS, how="left", indicator=True).query("_merge == 'left_only'")[KEYS]
    res = {"2022": len(k22), "2026": len(k26), "mantidas (mesmo UF/mun/zona/seção)": len(both),
           "só em 2022": len(so22), "só em 2026": len(so26)}
    if "local" not in d22 or "local" not in d26:
        res["obs"] = "sem coluna de local de votação: sem separar unidas/renumeradas"
        return res, so22, so26
    L = ["uf", "mun", "zona", "local"]
    o = so22.merge(d22[KEYS + ["local"]].drop_duplicates(KEYS), on=KEYS).groupby(L).size().rename("out")
    i = so26.merge(d26[KEYS + ["local"]].drop_duplicates(KEYS), on=KEYS).groupby(L).size().rename("in")
    g = pd.concat([o, i], axis=1).fillna(0).astype(int).reset_index()
    loc22 = set(map(tuple, d22[L].drop_duplicates().to_numpy()))
    loc26 = set(map(tuple, d26[L].drop_duplicates().to_numpy()))
    chave = list(map(tuple, g[L].to_numpy()))
    g["em22"] = [k in loc22 for k in chave]
    g["em26"] = [k in loc26 for k in chave]
    ambos = g["em22"] & g["em26"]
    res["renumeradas (mesmo local, outro número)"] = int(np.minimum(g.loc[ambos, "out"], g.loc[ambos, "in"]).sum())
    res["unidas (local mantido, menos seções)"] = int(np.maximum(g.loc[ambos, "out"] - g.loc[ambos, "in"], 0).sum())
    res["surgiram em local já existente"] = int(np.maximum(g.loc[ambos, "in"] - g.loc[ambos, "out"], 0).sum())
    res["excluídas (local de votação extinto)"] = int(g.loc[~g["em26"], "out"].sum())
    res["surgiram (local de votação novo)"] = int(g.loc[~g["em22"], "in"].sum())
    res["_total_surgiram"] = res["surgiram em local já existente"] + res["surgiram (local de votação novo)"]
    res["_total_excluidas"] = res["excluídas (local de votação extinto)"]
    return res, so22, so26


# --------------------------------------------------------------------------- gráficos
def estilo(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(True, color="#e3e3e3", linewidth=0.6)
    ax.set_axisbelow(True)


COR_CAND = {"a": "#c62828", "b": "#1565c0"}


def viradas(df, ignorar_ate=1.0):
    """Pontos (% das seções apuradas) em que a liderança muda entre A e B."""
    d = df["va"].cumsum().to_numpy(float) - df["vb"].cumsum().to_numpy(float)
    n = len(d)
    s = np.sign(d)
    nz = np.nonzero(s)[0]
    if len(nz) == 0:
        return [], 0
    mud = np.nonzero(np.diff(s[nz]))[0] + 1
    pos, lider = nz[mud], s[nz][mud]
    x = (pos + 1) / n * 100
    cedo = int((x < ignorar_ate).sum())
    return [(round(float(xi), 2), "A" if li > 0 else "B") for xi, li in zip(x, lider) if xi >= ignorar_ate], cedo


def curva_dupla(df, pontos=4000):
    """% acumulado dos votos válidos de A e de B conforme as seções vão sendo apuradas."""
    ca, cb = df["va"].cumsum().to_numpy(float), df["vb"].cumsum().to_numpy(float)
    n = len(df)
    x = np.arange(1, n + 1) / n * 100
    tot = np.maximum(ca + cb, 1)
    idx = np.unique(np.linspace(0, n - 1, min(pontos, n)).astype(int))
    return x[idx], (ca / tot * 100)[idx], (cb / tot * 100)[idx]


PAINEIS = [
    (2022, "real", "2022 na ordem real (2022)"),
    (2022, "reord", "2022 na ordem de 2026"),
    (2026, "real", "2026 na ordem real (2026)"),
    (2026, "reord", "2026 na ordem de 2022"),
]


def derivada(df, passo=0.5):
    """1ª derivada de cada curva de % dos votos válidos em relação ao % de seções apuradas.

    Unidade: pontos percentuais de voto válido por ponto percentual de seções apuradas.
    Calculada por diferenças finitas numa grade regular de `passo` % das seções.
    Como %A + %B = 100, a derivada de B é o espelho (sinal trocado) da de A.
    """
    ca, cb = df["va"].cumsum().to_numpy(float), df["vb"].cumsum().to_numpy(float)
    n = len(df)
    grade = np.arange(passo, 100 + 1e-9, passo)
    idx = np.clip(np.round(grade / 100 * n).astype(int) - 1, 0, n - 1)
    tot = np.maximum(ca[idx] + cb[idx], 1)
    pa, pb = ca[idx] / tot * 100, cb[idx] / tot * 100
    return grade, np.gradient(pa, grade), np.gradient(pb, grade)


def desenhar_derivada(res, cand_a, cand_b, passo, ylim, saida):
    dados = [derivada(res[(ano, tipo)], passo) for ano, tipo, _ in PAINEIS]
    if ylim is None:
        corte = max(5, 2 * passo)  # ignora o início, onde a derivada explode
        v = np.concatenate([np.abs(np.r_[da[g >= corte], db[g >= corte]]) for g, da, db in dados])
        lim = float(np.percentile(v, 98)) * 1.2 if v.size else 1.0
        ylim = (-lim, lim)
    fig, axs = plt.subplots(2, 2, figsize=(14, 9), sharex=True, sharey=True)
    for ax, (ano, tipo, titulo), (g, da, db) in zip(axs.ravel(), PAINEIS, dados):
        mk = "o" if passo >= 2 else None
        ax.plot(g, da, color=COR_CAND["a"], lw=1.6, marker=mk, ms=4, label=f"Candidato {cand_a}")
        ax.plot(g, db, color=COR_CAND["b"], lw=1.6, marker=mk, ms=4, label=f"Candidato {cand_b}")
        ax.set_xticks(np.arange(0, 101, max(passo, 10)))
        ax.axhline(0, color="#777", lw=0.8)
        ax.set_ylim(*ylim)
        ax.set_title(titulo, fontsize=12)
        ax.legend(frameon=False, loc="upper right")
        estilo(ax)
    for ax in axs[1]:
        ax.set_xlabel("% das seções apuradas")
    for ax in axs[:, 0]:
        ax.set_ylabel("d(% votos válidos) / d(% seções)  [p.p. por p.p.]")
    fig.suptitle("1ª derivada das curvas de apuração (13 x 22)", fontsize=14)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.text(0.01, 0.005, f"Diferenças finitas a cada {passo}% das seções; eixo Y limitado a "
             f"±{ylim[1]:.2f}, o início da apuração pode sair da escala. "
             f"A derivada de um candidato é o espelho da do outro (%13 + %22 = 100).",
             fontsize=9, color="#555")
    p = saida / "apuracao_13x22_derivada.png"
    fig.savefig(p, dpi=150)
    if not MOSTRAR:
        plt.close(fig)
    return p


def desenhar(res, cand_a, cand_b, ylim, saida):
    paineis = [
        (2022, "real", "2022 na ordem real (2022)"),
        (2022, "reord", "2022 na ordem de 2026"),
        (2026, "real", "2026 na ordem real (2026)"),
        (2026, "reord", "2026 na ordem de 2022"),
    ]
    fig, axs = plt.subplots(2, 2, figsize=(14, 9), sharex=True, sharey=True)
    for ax, (ano, tipo, titulo) in zip(axs.ravel(), paineis):
        x, pa, pb = curva_dupla(res[(ano, tipo)])
        ax.plot(x, pa, color=COR_CAND["a"], lw=2, label=f"Candidato {cand_a}")
        ax.plot(x, pb, color=COR_CAND["b"], lw=2, label=f"Candidato {cand_b}")
        ax.axhline(50, color="#777", lw=0.8)
        for val, dy in ((pa[-1], 8 if pa[-1] >= pb[-1] else -15), (pb[-1], 8 if pb[-1] > pa[-1] else -15)):
            ax.annotate(f"{val:.2f}%", (x[-1], val), xytext=(-4, dy), textcoords="offset points",
                        ha="right", fontsize=10, color="#222")
        ax.set_title(titulo, fontsize=12)
        if ylim:
            ax.set_ylim(*ylim)
        ax.legend(frameon=False, loc="upper right")
        estilo(ax)
    for ax in axs[1]:
        ax.set_xlabel("% das seções apuradas")
    for ax in axs[:, 0]:
        ax.set_ylabel("% dos votos válidos")
    nota = f"Eixo Y limitado a {ylim[0]:.0f}–{ylim[1]:.0f}%; o início da apuração pode sair da faixa." if ylim else ""
    fig.suptitle("Apuração do 2º turno presidencial: ordem real x ordem do outro ano", fontsize=14)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    if nota:
        fig.text(0.01, 0.005, nota, fontsize=9, color="#555")
    p = saida / "apuracao_13x22.png"
    fig.savefig(p, dpi=150)
    if not MOSTRAR:
        plt.close(fig)
    return p



# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(PASTA_DADOS), help="pasta com subpastas 2022/ e 2026/ (ZIPs por UF)")
    ap.add_argument("--saida", default=str(PASTA_SAIDA), help="pasta de saída")
    ap.add_argument("--baixar", action=argparse.BooleanOptionalAction, default=BAIXAR, help="baixa os ZIPs que faltarem")
    ap.add_argument("--refazer", action=argparse.BooleanOptionalAction, default=REFAZER, help="ignora o cache de seções")
    ap.add_argument("--ufs", default=",".join(UFS_USAR.replace(",", " ").split()), help="UFs separadas por vírgula (ZZ = exterior)")
    ap.add_argument("--cand-a", type=int, default=CAND_A, help="número do candidato A")
    ap.add_argument("--cand-b", type=int, default=CAND_B, help="número do candidato B")
    ap.add_argument("--passo-deriv", type=float, default=PASSO_DERIVADA, help="passo (em %% das seções) da derivada")
    ap.add_argument("--deriv-ylim", default=DERIV_YLIM, help='eixo Y da derivada, ex.: "-1,1", ou "auto"')
    ap.add_argument("--ylim", default=YLIM, help='limites do eixo Y em %%, ex.: "40,60", ou "auto"')
    a, _ = ap.parse_known_args()  # ignora argumentos extras que algumas IDEs/Jupyter injetam

    ufs = [u.strip().upper() for u in a.ufs.split(",") if u.strip()]
    base, saida = Path(a.dir), Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)

    checar_downloads(base, saida, ufs, a.cand_a, a.cand_b, a.refazer)
    d22 = carregar_ano(2022, base, ufs, a.cand_a, a.cand_b, saida, a.baixar, a.refazer)
    d26 = carregar_ano(2026, base, ufs, a.cand_a, a.cand_b, saida, a.baixar, a.refazer)

    r22_em_26 = ordem_emprestada(d22, d26)
    r26_em_22 = ordem_emprestada(d26, d22)
    res = {
        (2022, "reord"): r22_em_26,
        (2026, "reord"): r26_em_22,
        (2022, "real"): ordem_propria(d22),
        (2026, "real"): ordem_propria(d26),
    }

    print("\n=== Seções sem posição própria na ordem emprestada (horário imputado) ===")
    for nome, r in (("2022 na ordem de 2026", r22_em_26), ("2026 na ordem de 2022", r26_em_22)):
        print(f"{nome}:")
        print(r["origem"].value_counts().to_string(), "\n")

    print("=== Seções: 2022 x 2026 ===")
    comp, so22, so26 = comparar_secoes(d22, d26)
    for k, v in comp.items():
        if not k.startswith("_"):
            print(f"  {k}: {v:,}" if isinstance(v, int) else f"  {k}: {v}")
    if "_total_surgiram" in comp:
        print(f"\n  RESUMO -> surgiram: {comp['_total_surgiram']:,} | excluídas: {comp['_total_excluidas']:,} | "
              f"unidas: {comp['unidas (local mantido, menos seções)']:,}")
    print("  (classificação por local de votação é heurística; seção 'só em um ano' pode ser renumeração)")
    so22.assign(situacao="so_2022").to_csv(saida / "secoes_so_2022.csv", index=False)
    so26.assign(situacao="so_2026").to_csv(saida / "secoes_so_2026.csv", index=False)

    print("\n=== Valor final de cada curva (deve coincidir com o resultado oficial do ano) ===")
    for (ano, tipo), df in res.items():
        ca, cb = df["va"].sum(), df["vb"].sum()
        print(f"  {ano} ({tipo}): {a.cand_a}={ca / (ca + cb) * 100:.2f}%  margem={(ca - cb):,.0f}")

    print(f"\n=== Viradas (liderança muda) | A = {a.cand_a}, B = {a.cand_b}; mudanças antes de 1% das seções são ignoradas ===")
    for (ano, tipo), df in res.items():
        v, cedo = viradas(df)
        lider1 = "A" if (df["va"].cumsum().iloc[max(int(len(df) * 0.01) - 1, 0)] > df["vb"].cumsum().iloc[max(int(len(df) * 0.01) - 1, 0)]) else "B"
        txt = ", ".join(f"{x}% -> {l} na frente" for x, l in v) or "nenhuma"
        print(f"  {ano} ({tipo}): líder em 1% = {lider1}; {cedo} mudança(s) antes de 1%; viradas: {txt}")

    print("\n=== UFs mais frequentes nos primeiros e últimos 5% da ordem real (sanidade da ordem) ===")
    for ano in (2022, 2026):
        d = res[(ano, "real")]
        k = max(int(len(d) * 0.05), 1)
        print(f"  {ano} primeiros 5%: {d['uf'].head(k).value_counts().head(5).to_dict()}")
        print(f"  {ano} últimos 5%:   {d['uf'].tail(k).value_counts().head(5).to_dict()}")

    ylim = None if a.ylim.lower() == "auto" else tuple(float(v) for v in a.ylim.split(","))
    p1 = desenhar(res, a.cand_a, a.cand_b, ylim, saida)
    dylim = None if a.deriv_ylim.lower() == "auto" else tuple(float(v) for v in a.deriv_ylim.split(","))
    p2 = desenhar_derivada(res, a.cand_a, a.cand_b, a.passo_deriv, dylim, saida)
    print(f"\nGráficos: {p1}\n          {p2}")
    if MOSTRAR:
        plt.show()


if __name__ == "__main__":
    sys.exit(main())
