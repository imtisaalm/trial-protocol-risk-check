#!/usr/bin/env python3
"""Transparent protocol review demo using ClinicalTrials.gov API v2."""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from scipy.stats import mannwhitneyu
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "studies_raw.json"
RESULTS = ROOT / "results"
API_URL = "https://clinicaltrials.gov/api/v2/studies"

# Deliberate status quotas, not a representative registry sample.
QUOTAS = {
    "COMPLETED": 150,
    "TERMINATED": 50,
    "WITHDRAWN": 50,
    "SUSPENDED": 50,
}
INTERRUPTED = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
HIGH_RISK_THRESHOLD = 4

# These are illustrative review thresholds, not fitted coefficients.
WEIGHTS = {
    "narrow_age_window": 2,
    "long_exclusion_list": 2,
    "many_outcomes": 1,
    "high_enrollment_per_site": 2,
    "large_single_site_trial": 2,
}

HEADING = re.compile(
    r"\b(inclusion|exclusion)\s+criteria\b"
    r"[ \t]*(?:[:\-–—])?[ \t]*",
    re.IGNORECASE,
)
BULLET = re.compile(
    r"^\s*(?:[-*+•▪]|\d+[.)]|\([0-9]+\)|[A-Za-z][.)])\s+"
)


def make_session():
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update({
        "User-Agent": "trial-risk-learning-demo/1.0",
        "Accept": "application/json",
    })
    return session


def validate_studies(studies):
    """Reject incomplete or inappropriate caches instead of silently proceeding."""
    if not isinstance(studies, list):
        raise ValueError("Cache must contain a studies list.")

    seen = set()
    counts = dict.fromkeys(QUOTAS, 0)

    for study in studies:
        protocol = study.get("protocolSection", {})
        nct_id = protocol.get("identificationModule", {}).get("nctId")
        status = protocol.get("statusModule", {}).get("overallStatus")
        study_type = protocol.get("designModule", {}).get("studyType")

        if not nct_id or nct_id in seen:
            raise ValueError("Missing or duplicate NCT ID in input.")
        if study_type != "INTERVENTIONAL" or status not in QUOTAS:
            raise ValueError(f"Unexpected study type or status for {nct_id}.")

        seen.add(nct_id)
        counts[status] += 1

    if len(studies) < 250 or any(count == 0 for count in counts.values()):
        raise ValueError(
            "Need at least 250 unique studies and all four requested statuses. "
            "Delete the invalid cache or rerun with --refresh."
        )
    return counts


def load_studies(refresh=False):
    """
    Load the cached snapshot or fetch the requested status quotas.

    ClinicalTrials.gov API v2 does not support sorting on NCTId.
    Requests therefore use the API's default ordering, with pageToken
    pagination. The cache, rather than a guaranteed server ordering,
    provides a reproducible input snapshot.
    """
    if CACHE.exists() and not refresh:
        payload = json.loads(CACHE.read_text(encoding="utf-8"))
        validate_studies(payload["studies"])
        print(f"Using cache: {CACHE}")
        return payload

    studies = []
    seen = set()

    with make_session() as session:
        for status, target in QUOTAS.items():
            collected = 0
            token = None
            used_tokens = set()

            while collected < target:
                params = {
                    "format": "json",
                    "filter.overallStatus": status,
                    "query.term": "AREA[StudyType]INTERVENTIONAL",
                    "pageSize": min(100, target - collected),
                }
                if token:
                    params["pageToken"] = token

                response = session.get(
                    API_URL,
                    params=params,
                    timeout=(15, 90),
                )

                # Preserve the API's diagnostic text in any HTTP exception.
                # For example, the unsupported NCTId sort returns a useful
                # plain-text explanation alongside its HTTP 400 response.
                try:
                    response.raise_for_status()
                except requests.HTTPError as exc:
                    detail = response.text.strip()[:1000]
                    raise requests.HTTPError(
                        f"ClinicalTrials.gov API request failed for {status}: "
                        f"HTTP {response.status_code}. "
                        f"Response: {detail or '(empty response body)'}",
                        request=response.request,
                        response=response,
                    ) from exc

                page = response.json()
                if not isinstance(page, dict):
                    raise ValueError("API response must be a JSON object.")

                page_studies = page.get("studies")
                if not isinstance(page_studies, list):
                    raise ValueError(
                        "API response is missing a valid studies list."
                    )

                for study in page_studies:
                    protocol = study.get("protocolSection", {})
                    identification = protocol.get("identificationModule", {})
                    design = protocol.get("designModule", {})
                    actual_status = protocol.get("statusModule", {}).get(
                        "overallStatus"
                    )
                    nct_id = identification.get("nctId")

                    if (
                        nct_id
                        and nct_id not in seen
                        and design.get("studyType") == "INTERVENTIONAL"
                        and actual_status == status
                    ):
                        studies.append(study)
                        seen.add(nct_id)
                        collected += 1
                        if collected == target:
                            break

                next_token = page.get("nextPageToken")
                if collected == target or not next_token:
                    break
                if not isinstance(next_token, str):
                    raise ValueError("API returned an invalid page token.")
                if next_token in used_tokens:
                    raise RuntimeError("API returned a repeated page token.")
                used_tokens.add(next_token)
                token = next_token

            print(f"Fetched {collected} {status} studies.")
            if collected != target:
                raise RuntimeError(
                    f"Requested {target} {status} studies; received {collected}. "
                    "No partial cache was saved."
                )

    validate_studies(studies)
    payload = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "api_url": API_URL,
        "sampling": {
            "quotas": QUOTAS,
            "sort": None,
            "ordering": "API default ordering; no explicit sort parameter",
            "study_type": "INTERVENTIONAL",
            "query.term": "AREA[StudyType]INTERVENTIONAL",
            "note": (
                "Status-stratified convenience sample. API ordering and registry "
                "contents can change between downloads. Reuse the cached snapshot "
                "to reproduce the analysis."
            ),
        },
        # Each study object is retained as returned, without field reduction.
        "studies": studies,
    }

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CACHE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(CACHE)
    return payload


def sentence_count(text):
    """Approximate sentence-like criteria in prose, including semicolon lists."""
    text = re.sub(r"\s+", " ", text).strip(" \t:*#")
    if not text:
        return 0
    if re.fullmatch(
        r"(?:none|none specified|not applicable|n/?a)[.!]?",
        text,
        flags=re.IGNORECASE,
    ):
        return 0

    # Protect a few common abbreviations. This is not a clinical NLP model.
    protected = re.sub(
        r"\b(?:e\.g\.|i\.e\.|vs\.|Dr\.)",
        lambda match: match.group(0).replace(".", "<DOT>"),
        text,
        flags=re.IGNORECASE,
    )
    # Requiring whitespace after punctuation avoids splitting decimals.
    pieces = re.split(r"(?:[.!?]+[ \t]+|;[ \t]*)", protected)
    return sum(bool(re.search(r"\w", piece)) for piece in pieces)


def count_section(text):
    """
    Count each marked list item once; count unmarked prose by sentences.
    Wrapped lines after a list marker remain part of the preceding item.
    """
    count = 0
    prose = []
    in_list_item = False

    def flush_prose():
        nonlocal count
        if prose:
            count += sentence_count(" ".join(prose))
            prose.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush_prose()
            in_list_item = False
            continue

        if BULLET.match(raw_line):
            flush_prose()
            count += 1
            in_list_item = True
        elif in_list_item:
            # Continuation of a bullet or numbered item.
            continue
        else:
            prose.append(line)

    flush_prose()
    return count


def parse_eligibility(text):
    if not isinstance(text, str) or not text.strip():
        return {
            "criteria_text_length": np.nan,
            "inclusion_count": np.nan,
            "exclusion_count": np.nan,
            "eligibility_parse": "missing_text",
        }

    # Remove Markdown heading/emphasis tokens without removing list markers.
    cleaned = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]*", "", text)
    cleaned = cleaned.replace("**", "").replace("__", "")
    headings = list(HEADING.finditer(cleaned))
    sections = {"inclusion": [], "exclusion": []}

    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(cleaned)
        sections[heading.group(1).lower()].append(cleaned[heading.end():end])

    result = {"criteria_text_length": len(text)}
    for kind, chunks in sections.items():
        # An absent or empty section is unknown, not an observed zero.
        if chunks and any(chunk.strip() for chunk in chunks):
            result[f"{kind}_count"] = sum(count_section(chunk) for chunk in chunks)
        else:
            result[f"{kind}_count"] = np.nan

    result["eligibility_parse"] = (
        "both_headings"
        if sections["inclusion"] and sections["exclusion"]
        else "partial_headings"
        if headings
        else "unrecognized_headings"
    )
    return result


def parser_self_check():
    """Small deterministic checks for the central parsing requirement."""
    paragraph = (
        "Inclusion Criteria: Adults aged 18 to 65 years. "
        "Able to provide informed consent. "
        "Exclusion Criteria: Active infection. "
        "Pregnancy or breastfeeding; Prior treatment within 30 days."
    )
    numbered = (
        "Inclusion Criteria:\n1. Adult participants\n2. Written consent\n"
        "Exclusion Criteria:\n- Active infection\n- Pregnancy\n"
        "  confirmed at screening\n"
    )
    for text, expected in [(paragraph, (2, 3)), (numbered, (2, 2))]:
        parsed = parse_eligibility(text)
        observed = (parsed["inclusion_count"], parsed["exclusion_count"])
        if observed != expected:
            raise RuntimeError(f"Parser self-check failed: {observed} != {expected}")


def age_years(value):
    if not isinstance(value, str):
        return np.nan
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(years?|months?|weeks?|days?|hours?|minutes?)\s*",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return np.nan

    number = float(match.group(1))
    unit = match.group(2).lower().rstrip("s")
    factors = {
        "year": 1.0,
        "month": 1 / 12,
        "week": 7 / 365.25,
        "day": 1 / 365.25,
        "hour": 1 / (365.25 * 24),
        "minute": 1 / (365.25 * 24 * 60),
    }
    return number * factors[unit]


def nonnegative_number(value):
    if isinstance(value, bool):
        return np.nan
    try:
        number = float(value)
    except (TypeError, ValueError):
        return np.nan
    return number if np.isfinite(number) and number >= 0 else np.nan


def list_length(module, key):
    value = module.get(key)
    return len(value) if isinstance(value, list) else np.nan


def extract_trial(study):
    protocol = study["protocolSection"]
    identification = protocol.get("identificationModule", {})
    status_module = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    eligibility = protocol.get("eligibilityModule", {})
    outcomes = protocol.get("outcomesModule", {})
    locations = protocol.get("contactsLocationsModule", {}).get("locations")
    enrollment_info = design.get("enrollmentInfo", {})

    enrollment = nonnegative_number(enrollment_info.get("count"))
    # Count listed location entries, not assumed active recruiting sites.
    sites = len(locations) if isinstance(locations, list) and locations else np.nan
    minimum_age = age_years(eligibility.get("minimumAge"))
    maximum_age = age_years(eligibility.get("maximumAge"))
    age_window = maximum_age - minimum_age
    if not np.isfinite(age_window) or age_window < 0:
        age_window = np.nan

    sex = eligibility.get("sex", "UNKNOWN")
    healthy = eligibility.get("healthyVolunteers")
    healthy = healthy if isinstance(healthy, bool) else None

    row = {
        "nct_id": identification["nctId"],
        "title": identification.get("briefTitle", ""),
        "status": status_module["overallStatus"],
        "enrollment": enrollment,
        "enrollment_type": enrollment_info.get("type", "UNKNOWN"),
        "number_of_sites": sites,
        "phases": "|".join(design.get("phases") or []) or "UNKNOWN",
        "minimum_age_years": minimum_age,
        "maximum_age_years": maximum_age,
        "age_window_years": age_window,
        "sex": sex,
        "sex_restricted": sex != "ALL" if sex in {"ALL", "FEMALE", "MALE"} else None,
        "healthy_volunteers": healthy,
        "primary_outcome_count": list_length(outcomes, "primaryOutcomes"),
        "secondary_outcome_count": list_length(outcomes, "secondaryOutcomes"),
        "enrollment_per_site": enrollment / sites if np.isfinite(sites) else np.nan,
    }
    row.update(parse_eligibility(eligibility.get("eligibilityCriteria")))
    return row


def score_trial(row):
    primary = row["primary_outcome_count"]
    secondary = row["secondary_outcome_count"]

    # Three-valued logic: true, false, or unassessable.
    if np.isfinite(primary) and primary >= 3:
        many_outcomes = True
    elif np.isfinite(primary) and np.isfinite(secondary):
        many_outcomes = bool(primary + secondary >= 10)
    else:
        many_outcomes = None

    def threshold(value, limit, comparison):
        return bool(comparison(value, limit)) if np.isfinite(value) else None

    sites = row["number_of_sites"]
    enrollment = row["enrollment"]
    if np.isfinite(sites) and sites != 1:
        large_single_site = False
    elif np.isfinite(sites) and np.isfinite(enrollment):
        large_single_site = bool(enrollment >= 300)
    else:
        large_single_site = None

    signals = {
        "narrow_age_window": threshold(row["age_window_years"], 10, lambda x, y: x < y),
        "long_exclusion_list": threshold(row["exclusion_count"], 15, lambda x, y: x >= y),
        "many_outcomes": many_outcomes,
        "high_enrollment_per_site": threshold(
            row["enrollment_per_site"], 200, lambda x, y: x > y
        ),
        "large_single_site_trial": large_single_site,
    }

    flags = [name for name, value in signals.items() if value is True]
    unknown = [name for name, value in signals.items() if value is None]
    score = sum(WEIGHTS[name] for name in flags)
    return {
        "risk_score": score,
        "high_risk": score >= HIGH_RISK_THRESHOLD,
        "flags": ";".join(flags),
        "unassessable_signals": ";".join(unknown),
        "signals_assessed": len(signals) - len(unknown),
        "all_signals_assessable": not unknown,
    }


def summarize(frame):
    return {
        "n": int(len(frame)),
        "median_score": float(frame["risk_score"].median()) if len(frame) else None,
        "high_risk_n": int(frame["high_risk"].sum()),
        "high_risk_share": float(frame["high_risk"].mean()) if len(frame) else None,
        "all_signals_assessable_n": int(frame["all_signals_assessable"].sum()),
    }


def compare_groups(frame):
    completed = frame.loc[frame["status"] == "COMPLETED", "risk_score"].to_numpy()
    interrupted = frame.loc[frame["status"].isin(INTERRUPTED), "risk_score"].to_numpy()

    if not len(completed) or not len(interrupted):
        return {"available": False, "reason": "One comparison group is empty."}

    # Asymptotic calculation handles ties; exact mode is inappropriate here.
    if np.all(np.concatenate([completed, interrupted]) == completed[0]):
        u = len(completed) * len(interrupted) / 2
        p = 1.0
    else:
        result = mannwhitneyu(
            interrupted,
            completed,
            alternative="two-sided",
            method="asymptotic",
            use_continuity=True,
        )
        u, p = float(result.statistic), float(result.pvalue)

    # Positive values indicate higher scores among interrupted studies.
    rank_biserial = 2 * u / (len(interrupted) * len(completed)) - 1

    if abs(rank_biserial) < 0.10:
        interpretation = (
            "The observed separation is weak or absent by the demo's "
            "descriptive effect-size threshold, regardless of the p-value."
        )
    elif rank_biserial < 0:
        interpretation = (
            "Scores are higher in the opposite direction: completed studies "
            "tend to score higher. This does not support the intended signal."
        )
    elif p >= 0.05:
        interpretation = (
            "Interrupted studies tend to score higher, but evidence is inconclusive "
            "in this sample. This is not predictive validation."
        )
    else:
        interpretation = (
            "Interrupted studies tend to score higher in this exploratory sample. "
            "The association does not establish causality or predictive validity."
        )

    return {
        "available": True,
        "completed_n": int(len(completed)),
        "interrupted_n": int(len(interrupted)),
        "u_interrupted_first": float(u),
        "p_value_two_sided": float(p),
        "method": "asymptotic, tie-corrected, continuity correction",
        "rank_biserial_interrupted_higher": float(rank_biserial),
        "interpretation": interpretation,
    }


def make_chart(frame):
    colors = ["#587A8A", "#B18270"]
    groups = [
        frame.loc[frame["status"] == "COMPLETED", "risk_score"].to_numpy(),
        frame.loc[frame["status"].isin(INTERRUPTED), "risk_score"].to_numpy(),
    ]
    labels = ["Completed", "Terminated / withdrawn /\nsuspended"]
    rng = np.random.default_rng(42)

    with plt.rc_context({
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "axes.labelcolor": "#34434B",
        "text.color": "#26343C",
        "xtick.color": "#4C5A62",
        "ytick.color": "#4C5A62",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
        "axes.spines.bottom": False,
    }):
        fig, ax = plt.subplots(figsize=(9, 6.2), facecolor="white")
        fig.subplots_adjust(left=0.11, right=0.96, top=0.78, bottom=0.24)

        for position, (values, color) in enumerate(zip(groups, colors), start=1):
            boxes = ax.boxplot(
                values,
                positions=[position],
                widths=0.40,
                patch_artist=True,
                showfliers=False,
                medianprops={"color": "#26343C", "linewidth": 2},
                whiskerprops={"color": color, "linewidth": 1.3},
                capprops={"color": color, "linewidth": 1.3},
            )
            boxes["boxes"][0].set(facecolor=color, edgecolor=color, alpha=0.22)
            ax.scatter(
                position + rng.uniform(-0.16, 0.16, len(values)),
                values,
                color=color,
                alpha=0.30,
                s=16,
                linewidths=0,
                zorder=3,
            )

        ax.axhline(
            HIGH_RISK_THRESHOLD,
            color="#8D7959",
            linestyle=(0, (4, 4)),
            linewidth=1.2,
            label=f"Review threshold: {HIGH_RISK_THRESHOLD}",
        )
        ax.set_xticks([1, 2])
        ax.set_xticklabels([
            f"{label}\n(n = {len(values):,})"
            for label, values in zip(labels, groups)
        ])
        ax.set_xlim(0.5, 2.5)
        ax.set_ylim(-0.4, sum(WEIGHTS.values()) + 0.5)
        ax.set_yticks(range(sum(WEIGHTS.values()) + 1))
        ax.set_ylabel("Heuristic review score (0 to 9)", labelpad=12)
        ax.set_xlabel("Registry status group", labelpad=12)
        ax.yaxis.grid(True, color="#E8ECEE", linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(axis="both", length=0)

        fig.text(0.11, 0.94, "Protocol review scores by trial status",
                 fontsize=18, weight="bold")
        fig.text(0.11, 0.885,
                 "Exploratory registry comparison, not a prediction of trial failure",
                 fontsize=10.5, color="#5C6970")

        handles = [
            Patch(facecolor=colors[0], alpha=0.5, label="Completed"),
            Patch(facecolor=colors[1], alpha=0.5, label="Interrupted status"),
            ax.lines[-1],
        ]
        fig.legend(handles=handles, loc="upper left",
                   bbox_to_anchor=(0.10, 0.855), ncol=3, frameon=False, fontsize=9)
        fig.text(
            0.11, 0.06,
            "Dots show trials with horizontal jitter. Boxes show the median and IQR.\n"
            "Whiskers extend to observations within 1.5 × IQR. Missing signals add no points.",
            fontsize=9, color="#5C6970",
        )
        fig.savefig(RESULTS / "risk_by_status.png", dpi=320, facecolor="white")
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true",
                        help="Replace the cache with a fresh API sample.")
    args = parser.parse_args()

    parser_self_check()
    payload = load_studies(refresh=args.refresh)
    frame = pd.DataFrame(extract_trial(study) for study in payload["studies"])
    scores = pd.DataFrame(score_trial(row) for _, row in frame.iterrows())
    frame = pd.concat([frame, scores], axis=1)

    RESULTS.mkdir(parents=True, exist_ok=True)
    frame.to_csv(RESULTS / "trial_features.csv", index=False)
    frame.sort_values(
        ["risk_score", "nct_id"],
        ascending=[False, True],
        kind="stable",
    ).head(10).to_csv(RESULTS / "top_risk_trials.csv", index=False)

    completed = frame[frame["status"] == "COMPLETED"]
    interrupted = frame[frame["status"].isin(INTERRUPTED)]
    complete_features = frame[frame["all_signals_assessable"]]

    metrics = {
        "analysis_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_fetched_at_utc": payload.get("fetched_at_utc"),
        "sampling": payload.get("sampling"),
        "n_total": int(len(frame)),
        "high_risk_threshold": HIGH_RISK_THRESHOLD,
        "maximum_score": sum(WEIGHTS.values()),
        "weights": WEIGHTS,
        "groups": {
            "COMPLETED": summarize(completed),
            "TERMINATED_WITHDRAWN_SUSPENDED": summarize(interrupted),
        },
        "individual_statuses": {
            status: summarize(frame[frame["status"] == status])
            for status in QUOTAS
        },
        "comparison": compare_groups(frame),
        "complete_feature_sensitivity": {
            "n": int(len(complete_features)),
            "groups": {
                "COMPLETED": summarize(
                    complete_features[complete_features["status"] == "COMPLETED"]
                ),
                "TERMINATED_WITHDRAWN_SUSPENDED": summarize(
                    complete_features[complete_features["status"].isin(INTERRUPTED)]
                ),
            },
            "comparison": compare_groups(complete_features),
        },
        "eligibility_parse_counts": {
            str(key): int(value)
            for key, value in frame["eligibility_parse"].value_counts().items()
        },
        "missing_feature_counts": {
            column: int(frame[column].isna().sum())
            for column in [
                "enrollment", "number_of_sites", "age_window_years",
                "inclusion_count", "exclusion_count",
                "primary_outcome_count", "secondary_outcome_count",
                "enrollment_per_site",
            ]
        },
        "caution": (
            "Current registry snapshots include post-baseline information. "
            "These results are exploratory associations, not prospective validation."
        ),
    }
    (RESULTS / "metrics.json").write_text(
        json.dumps(metrics, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    make_chart(frame)

    print("\nGroup summaries:")
    print(pd.DataFrame(metrics["groups"]).T.to_string())
    print("\n" + metrics["comparison"]["interpretation"])
    print(f"\nResults saved to {RESULTS}")


if __name__ == "__main__":
    main()
