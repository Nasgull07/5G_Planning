#!/usr/bin/env python3
"""Generate a standalone HTML delay report for one K/S experiment."""

import argparse
from collections import Counter
import csv
import html
import io
import math
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


ROOT = Path(__file__).resolve().parent.parent
GROUPS = ("only_option", "best_with_alternatives", "alternative")
LABELS = ("Only option", "Best with alternatives", "Feasible alternative")


def input_paths(k, s, root=ROOT):
    if k < 1 or s < 0:
        raise ValueError("K debe ser positivo y S no negativo")
    tag = f"k{k}" + (f"_s{s}" if s else "")
    directory = root / "output" / "delay"
    paths = [directory / f"rrh_delay_{tag}_summary.csv",
             directory / f"rrh_pool_{tag}_links.csv"]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Faltan CSV de entrada:\n" + "\n".join(missing))
    return *paths, directory / f"rrh_delay_{tag}_report.html"


def read_csv(path, columns):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not columns.issubset(reader.fieldnames or []):
            raise ValueError(f"{path.name}: faltan columnas requeridas")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path.name}: archivo vacío")
    return rows


def boolean(value):
    value = str(value).strip().lower()
    if value not in {"true", "false", "1", "0", "t", "f"}:
        raise ValueError(f"Booleano inválido: {value}")
    return value in {"true", "1", "t"}


def number(value):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("Distancias y RTT deben ser finitos y no negativos")
    return value


def load_inputs(summary_path, links_path, k, s):
    summaries = read_csv(summary_path, {
        "rrh_id", "is_synthetic", "service_status", "nearest_bbu_pool",
        "nearest_distance_km", "nearest_rtt_us", "best_feasible_bbu_pool",
        "feasible_pool_count", "feasible_pools"})
    rrhs = {row["rrh_id"]: row for row in summaries}
    if "" in rrhs or len(rrhs) != len(summaries):
        raise ValueError("Identificadores RRH vacíos o duplicados")
    if sum(boolean(row["is_synthetic"]) for row in summaries) != s:
        raise ValueError("S no coincide con el resumen")
    rows = read_csv(links_path, {"rrh_id", "bbu_pool", "distance_km", "rtt_us",
                                  "is_feasible", "is_best_feasible"})
    pools = sorted({row["bbu_pool"] for row in rows})
    if len(pools) != k or "" in pools:
        raise ValueError("K no coincide con los pools de la tabla de enlaces")
    links = {}
    for row in rows:
        pair = row["rrh_id"], row["bbu_pool"]
        if pair[0] not in rrhs or pair in links:
            raise ValueError(f"Enlace desconocido o duplicado: {pair}")
        for field in ("distance_km", "rtt_us"):
            number(row[field])
        boolean(row["is_feasible"])
        boolean(row["is_best_feasible"])
        links[pair] = row
    if len(links) != len(rrhs) * k:
        raise ValueError("Matriz de enlaces incompleta")
    groups = {pool: {group: [] for group in GROUPS} for pool in pools}
    for identifier, rrh in rrhs.items():
        options = {pool for pool in pools if boolean(links[identifier, pool]["is_feasible"])}
        declared = [pool for pool in rrh["feasible_pools"].split(";") if pool]
        best = rrh["best_feasible_bbu_pool"]
        chosen = {pool for pool in pools if boolean(links[identifier, pool]["is_best_feasible"])}
        if (len(options) != int(rrh["feasible_pool_count"])
                or options != set(declared) or len(declared) != len(options)
                or rrh["service_status"] != ("served" if options else "unserved")
                or (options and best not in options) or (not options and best)
                or chosen != ({best} if best else set())):
            raise ValueError(f"Resumen y enlaces incoherentes para {identifier}")
        nearest = rrh["nearest_bbu_pool"]
        if nearest not in pools:
            raise ValueError(f"Pool más cercano desconocido para {identifier}")
        for summary_field, link_field in (("nearest_distance_km", "distance_km"),
                                          ("nearest_rtt_us", "rtt_us")):
            value = number(rrh[summary_field])
            if (not math.isclose(value, float(links[identifier, nearest][link_field]))
                    or not math.isclose(value, min(float(links[identifier, p][link_field]) for p in pools))):
                raise ValueError(f"Mínimo incoherente para {identifier}")
        if best and not math.isclose(float(links[identifier, best]["rtt_us"]),
                                     min(float(links[identifier, p]["rtt_us"]) for p in options)):
            raise ValueError(f"Pool elegido no óptimo para {identifier}")
        for pool in options:
            group = ("only_option" if len(options) == 1 else
                     "best_with_alternatives" if pool == best else "alternative")
            groups[pool][group].append(identifier)
    return rrhs, pools, groups


def empirical_cdf(values):
    """F(x) = count(value <= x) / N, including ties at their full mass."""
    counts = Counter(values)
    xs, ys, cumulative = [0.0], [0.0], 0
    for value in sorted(counts):
        cumulative += counts[value]
        xs.append(value)
        ys.append(cumulative / len(values))
    return xs, ys


def figure_svg(fig):
    buffer = io.StringIO()
    fig.savefig(buffer, format="svg", bbox_inches="tight")
    plt.close(fig)
    return "<svg" + buffer.getvalue().split("<svg", 1)[1]


def charts(rrhs, pools, groups):
    distances = [float(row["nearest_distance_km"]) for row in rrhs.values()]
    xs, ys = empirical_cdf(distances)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.step(xs, ys, where="post", label=f"K={len(pools)} · N={len(rrhs)}", color="#245b96")
    unserved = sorted({float(row["nearest_distance_km"]) for row in rrhs.values()
                       if row["service_status"] == "unserved"})
    if unserved:
        cumulative = dict(zip(xs, ys))
        ax.scatter(unserved, [cumulative[d] for d in unserved], color="#c0392b",
                   marker="x", label="Unserved RRHs", zorder=3)
    ax.set(xlabel="Distance to nearest pool (km)", ylabel="F(d) = P(D ≤ d)",
           title="Empirical CDF · all RRHs", ylim=(0, 1.03), xlim=(0, max(xs) * 1.05 or 1))
    ax.grid(alpha=.25)
    ax.legend()
    cdf = figure_svg(fig)
    fig, ax = plt.subplots(figsize=(max(10, len(pools) * .5), 4))
    for index, (group, label, color) in enumerate(zip(GROUPS, LABELS, ("#c0392b", "#245b96", "#2c8c73"))):
        ax.bar([i + (index - 1) * .25 for i in range(len(pools))],
               [len(groups[p][group]) for p in pools], width=.25, label=label, color=color)
    ax.set_xticks(range(len(pools)), pools, rotation=45, ha="right")
    ax.set(ylabel="Number of RRHs", title="Connection options per BBU Pool")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.02), ncol=3)
    pool_chart = figure_svg(fig)
    identifiers = sorted(rrhs)
    fig, ax = plt.subplots(figsize=(max(12, min(30, len(rrhs) * .05)), 4))
    ax.bar(range(len(identifiers)), [int(rrhs[r]["feasible_pool_count"]) for r in identifiers],
           color=["#c0392b" if rrhs[r]["service_status"] == "unserved" else "#245b96"
                  for r in identifiers], width=.85)
    missing = [i for i, r in enumerate(identifiers) if rrhs[r]["service_status"] == "unserved"]
    if missing:
        ax.scatter(missing, [0] * len(missing), color="#c0392b", marker="x", label="Unserved", zorder=3)
        ax.legend()
    stride = max(1, math.ceil(len(identifiers) / 30))
    positions = list(range(0, len(identifiers), stride))
    ax.set_xticks(positions, [identifiers[i] for i in positions], rotation=90, fontsize=8)
    ax.set(ylabel="Number of feasible pools", xlabel="RRH (ordered by ID)",
           title="Alternatives per RRH · one bar per RRH", ylim=(-.2, len(pools) + .5))
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    return cdf, pool_chart, figure_svg(fig)


def table(headers, rows):
    def cells(tag, values):
        return "".join(f"<{tag}>{html.escape(str(value))}</{tag}>" for value in values)
    return ("<div class='scroll'><table><thead><tr>" + cells("th", headers)
            + "</tr></thead><tbody>" + "".join("<tr>" + cells("td", row) + "</tr>" for row in rows)
            + "</tbody></table></div>")


def generate_report(rrhs, pools, groups, output, k, s):
    cdf, pool_chart, rrh_chart = charts(rrhs, pools, groups)
    served = sum(row["service_status"] == "served" for row in rrhs.values())
    distances = [float(row["nearest_distance_km"]) for row in rrhs.values()]
    rtts = [float(row["nearest_rtt_us"]) for row in rrhs.values()]
    metrics = table(["Metric", "Value"], [
        ("Pools", k), ("Real / synthetic RRHs", f"{len(rrhs)-s} / {s}"),
        ("Served / unserved", f"{served} / {len(rrhs)-served}"),
        ("Coverage", f"{served / len(rrhs):.2%}"),
        ("Nearest-pool distance: mean / maximum (km)", f"{mean(distances):.3f} / {max(distances):.3f}"),
        ("Nearest-pool RTT: mean / maximum (µs)", f"{mean(rtts):.3f} / {max(rtts):.3f}")])
    pool_table = table(["Pool", *LABELS, "Total feasible"],
                       [(p, *[len(groups[p][g]) for g in GROUPS],
                         sum(len(groups[p][g]) for g in GROUPS)) for p in pools])
    lists = ""
    for pool in pools:
        lists += f"<details><summary>{html.escape(pool)} · RRH lists</summary>"
        for group, label in zip(GROUPS, LABELS):
            lists += f"<p><b>{label} ({len(groups[pool][group])})</b><br>"
            lists += html.escape(", ".join(sorted(groups[pool][group])) or "None") + "</p>"
        lists += "</details>"
    rrh_table = table(["RRH", "Status", "Best feasible pool", "Number of options", "Feasible pools"],
                      [(r, rrhs[r]["service_status"], rrhs[r]["best_feasible_bbu_pool"] or "—",
                        rrhs[r]["feasible_pool_count"], rrhs[r]["feasible_pools"] or "—") for r in sorted(rrhs)])
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Delay · K={k}, S={s}</title><style>
body{{font:16px system-ui;max-width:1200px;margin:30px auto;padding:0 20px;color:#203047}}
h1,h2{{color:#163b65}}section{{margin:35px 0}}.scroll,.chart{{overflow-x:auto}}
.chart svg{{width:100%;height:auto;min-width:650px}}table{{border-collapse:collapse;width:100%}}
td,th{{padding:9px;border-bottom:1px solid #ddd;text-align:left}}th{{background:#edf3f9}}
details{{padding:12px;border-bottom:1px solid #ddd}}summary{{cursor:pointer;font-weight:bold}}
</style><h1>Delay analysis · K={k}, S={s}</h1>
<p>Results for a single experiment; assignments are not recalculated and pools are not selected.</p>
<section><h2>Summary</h2>{metrics}
<p>The distances and RTTs above include all RRHs, including unserved ones.
The RTT threshold and experiment constants are not inferred: they are not stored in the CSV files.</p></section>
<section><h2>CDF of distance to the nearest pool</h2>
<p>F(d) is the proportion of all RRHs whose distance to the nearest pool is less than or equal
to d. It includes real and synthetic, served and unserved RRHs; each RRH has weight 1/N.
Red crosses mark the distances of unserved RRHs.</p><div class="chart">{cdf}</div></section>
<section><h2>Options per BBU Pool</h2><div class="chart">{pool_chart}</div>{pool_table}
<p>The three groups are mutually exclusive within each pool. An RRH may appear under several pools
as an alternative. These counts do not represent capacity or load balancing.</p>{lists}</section>
<section><h2>Options per RRH</h2><div class="chart">{rrh_chart}</div>
<p>Each RRH has one bar; axis labels are spaced out when there are many identifiers.
The table retains all identifiers and their alternatives.</p>{rrh_table}</section></html>"""
    output.write_text(page, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-k", "--k-pools", required=True, type=int)
    parser.add_argument("-s", "--synthetic-count", default=0, type=int)
    args = parser.parse_args()
    try:
        summary, links, output = input_paths(args.k_pools, args.synthetic_count)
        rrhs, pools, groups = load_inputs(summary, links, args.k_pools, args.synthetic_count)
        generate_report(rrhs, pools, groups, output, args.k_pools, args.synthetic_count)
    except (OSError, ValueError, csv.Error) as error:
        parser.error(str(error))
    print(f"Report: {output}")
    print(f"RRHs: {len(rrhs)} | Pools: {len(pools)}")


if __name__ == "__main__":
    main()
