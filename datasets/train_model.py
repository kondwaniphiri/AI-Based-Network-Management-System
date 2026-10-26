"""
Phase 5 + 6 – AI Model Training & Decision Output
===================================================
Reads processed_dataset.csv, trains a Decision Tree classifier and a
Linear Regression latency predictor, prints insights, and saves
model artifacts + a human-readable insights file.

Usage
-----
    python datasets/train_model.py
    python datasets/train_model.py --input datasets/processed_dataset.csv

Outputs
-------
    datasets/model_insights.txt   – rules + recommendations
    datasets/model.pkl            – saved Decision Tree (pickle)
    datasets/regression.pkl       – saved Linear Regression (pickle)
"""

import argparse
import os
import pickle
import sys
import textwrap
from io import StringIO

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE   = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT      = os.path.join(_HERE, "processed_dataset.csv")
INSIGHTS_PATH      = os.path.join(_HERE, "model_insights.txt")
MODEL_PKL_PATH     = os.path.join(_HERE, "model.pkl")
REGRESSION_PKL_PATH= os.path.join(_HERE, "regression.pkl")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _check_imports():
    missing = []
    for pkg in ("sklearn", "numpy", "pandas"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        sys.exit(
            f"[ERROR] Missing packages: {missing}\n"
            "        Install with:  pip install scikit-learn numpy pandas"
        )

def _load_data(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        sys.exit(
            f"[ERROR] Dataset not found: {path}\n"
            "        Run process_packets.py first."
        )
    df = pd.read_csv(path)
    print(f"[+] Loaded {len(df):,} rows from {path}")
    return df

def _encode_features(df: pd.DataFrame) -> pd.DataFrame:
    """Encode categorical columns as integers."""
    from sklearn.preprocessing import LabelEncoder
    df = df.copy()
    for col in ["protocol", "source", "destination"]:
        if col in df.columns:
            le = LabelEncoder()
            df[f"{col}_enc"] = le.fit_transform(df[col].fillna("unknown").astype(str))
    return df

# ---------------------------------------------------------------------------
# Decision Tree classifier (normal / congested / high_loss)
# ---------------------------------------------------------------------------
def train_classifier(df: pd.DataFrame) -> tuple:
    from sklearn.tree import DecisionTreeClassifier, export_text
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
    from sklearn.model_selection import train_test_split

    df = _encode_features(df)

    feature_cols = [c for c in ["protocol_enc", "packet_size", "rate_per_sec",
                                 "inter_arrival_ms", "latency_ms", "packet_size_norm"]
                    if c in df.columns]
    label_col = "label"

    df = df.dropna(subset=feature_cols + [label_col]).reset_index(drop=True)
    X = df[feature_cols].values
    y = df[label_col].values

    # Hold out 20% for evaluation. The fixed seed makes repeated runs
    # comparable; stratify preserves class proportions in the test set.
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # max_depth limits tree complexity; min_samples_leaf reduces overfitting.
    # Increase them only when the dataset is large enough to support detail.
    clf = DecisionTreeClassifier(max_depth=6, min_samples_leaf=5, random_state=42)
    clf.fit(X_train, y_train)

    y_pred = clf.predict(X_test)
    acc    = accuracy_score(y_test, y_pred)
    report = classification_report(y_test, y_pred)
    cm     = confusion_matrix(y_test, y_pred, labels=clf.classes_)

    # Feature importances
    fi = sorted(zip(feature_cols, clf.feature_importances_),
                key=lambda x: x[1], reverse=True)

    # Human-readable tree rules (truncated to depth 4 for readability)
    tree_rules = export_text(clf, feature_names=feature_cols, max_depth=4)

    return clf, acc, report, cm, fi, tree_rules, feature_cols

# ---------------------------------------------------------------------------
# Linear Regression latency predictor
# ---------------------------------------------------------------------------
def train_regression(df: pd.DataFrame) -> tuple:
    from sklearn.linear_model import LinearRegression
    from sklearn.metrics import mean_absolute_error, r2_score
    from sklearn.model_selection import train_test_split

    df = _encode_features(df)

    feat_cols = [c for c in ["protocol_enc", "packet_size", "rate_per_sec",
                              "inter_arrival_ms", "packet_size_norm"]
                 if c in df.columns]
    target = "latency_ms"

    df = df.dropna(subset=feat_cols + [target]).reset_index(drop=True)
    X = df[feat_cols].values
    y = df[target].values

    # Use the same 80/20 split and seed so model evaluations are repeatable.
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    reg = LinearRegression()
    reg.fit(X_train, y_train)

    y_pred = reg.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    r2  = r2_score(y_test, y_pred)

    coefs = list(zip(feat_cols, reg.coef_))

    return reg, mae, r2, coefs, feat_cols

# ---------------------------------------------------------------------------
# Rule-based AI decisions (Phase 6)
# ---------------------------------------------------------------------------
def generate_decisions(df: pd.DataFrame, fi: list, coefs: list) -> list[str]:
    """Translate model insights into actionable network recommendations."""
    decisions = []

    # Decision 1: UDP rate threshold
    max_udp_rate = df.loc[df["protocol"] == "UDP", "rate_per_sec"].quantile(0.9) \
                   if "protocol" in df.columns else 0
    if max_udp_rate > 20:
        decisions.append(
            f"HIGH UDP RATE detected (90th percentile: {max_udp_rate:.1f} pkt/s).\n"
            f"  → Recommendation: Reduce ESP32-3 UDP transmission rate.\n"
            f"     Increase udpInterval to random(2000, 5000) in esp32_device 3.ino.\n"
            f"     Or switch ESP32-3 from UDP to TCP to gain flow control."
        )

    # Decision 2: Large packet latency
    top_fi = fi[0][0] if fi else ""
    if "packet_size" in top_fi:
        decisions.append(
            "PACKET SIZE is the top predictor of latency.\n"
            "  → Recommendation: Fragment large ESP32 payloads into ≤ 512 byte chunks\n"
            "     to reduce per-hop queuing delay."
        )

    # Decision 3: Congestion → protocol switch
    congested_pct = (df["label"] == "congested").mean() * 100 if "label" in df.columns else 0
    if congested_pct > 15:
        decisions.append(
            f"CONGESTION rate is {congested_pct:.1f}% of captured traffic.\n"
            f"  → Recommendation: Prioritise HTTP (TCP) over UDP on the shared switch.\n"
            f"     Implement QoS or rate-limit raw UDP to max 10 pkt/s on the server."
        )

    # Decision 4: Latency regression coefficient check
    for feat, coef in coefs:
        if "rate" in feat and coef > 1.0:
            decisions.append(
                f"REGRESSION shows each +1 pkt/s increase in {feat} adds "
                f"+{coef:.2f} ms latency.\n"
                f"  → Recommendation: Cap transmission rates to reduce queuing latency."
            )
            break

    if not decisions:
        decisions.append(
            "No critical thresholds exceeded in collected data.\n"
            "  → Network appears stable. Continue monitoring with longer capture duration."
        )

    return decisions

# ---------------------------------------------------------------------------
# Save insights
# ---------------------------------------------------------------------------
def save_insights(path: str, acc: float, report: str, cm: "np.ndarray",
                  fi: list, tree_rules: str,
                  mae: float, r2: float, coefs: list,
                  decisions: list[str]) -> None:
    lines = [
        "=" * 70,
        "  IoT Network AI Model Insights",
        f"  Generated by train_model.py",
        "=" * 70,
        "",
        "── DECISION TREE CLASSIFIER ──────────────────────────────────────",
        f"  Target:   label  (normal / congested / high_loss)",
        f"  Accuracy: {acc * 100:.2f}%",
        "",
        "  Classification Report:",
    ]
    for line in report.strip().splitlines():
        lines.append("    " + line)
    lines += [
        "",
        "  Confusion Matrix (rows=actual, cols=predicted):",
        "    " + str(cm).replace("\n", "\n    "),
        "",
        "  Feature Importances (ranked):",
    ]
    for feat, imp in fi:
        bar = "█" * int(imp * 40)
        lines.append(f"    {feat:<25s}  {imp:.4f}  {bar}")
    lines += [
        "",
        "  Decision Tree Rules (depth ≤ 4):",
    ]
    for line in tree_rules.strip().splitlines():
        lines.append("    " + line)
    lines += [
        "",
        "── LINEAR REGRESSION (latency_ms predictor) ──────────────────────",
        f"  Mean Absolute Error : {mae:.2f} ms",
        f"  R² Score            : {r2:.4f}",
        "",
        "  Coefficients (feature → ms per unit increase):",
    ]
    for feat, coef in coefs:
        lines.append(f"    {feat:<25s}  {coef:+.4f} ms/unit")
    lines += [
        "",
        "── AI DECISIONS & RECOMMENDATIONS (Phase 6) ──────────────────────",
    ]
    for i, dec in enumerate(decisions, 1):
        lines.append(f"  [{i}] " + dec.replace("\n", "\n      "))
        lines.append("")
    lines += [
        "=" * 70,
        "  NOTE: AI identifies patterns only.",
        "  Changes must be applied manually to ESP32 firmware or server config.",
        "=" * 70,
    ]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[+] Insights saved → {path}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    _check_imports()

    parser = argparse.ArgumentParser(description="Phase 5+6 – Train AI model")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="processed_dataset.csv path")
    parser.add_argument("--legacy-heuristics", action="store_true",
                        help="Explicitly run the historical rule-label reproduction experiment")
    args = parser.parse_args()

    if not args.legacy_heuristics:
        parser.error("Use datasets/train_windows.py for future-outcome prediction. "
                     "--legacy-heuristics reproduces rule labels and does not validate congestion prediction.")

    df = _load_data(args.input)

    # Check minimum rows
    if len(df) < 50:
        sys.exit("[ERROR] Dataset too small (< 50 rows). Capture more traffic first.")

    print("[+] Training Decision Tree classifier …")
    clf, acc, report, cm, fi, tree_rules, _ = train_classifier(df)

    print("[+] Training Linear Regression (latency predictor) …")
    reg, mae, r2, coefs, _ = train_regression(df)

    print("[+] Generating AI decisions …")
    decisions = generate_decisions(df, fi, coefs)

    print("\n" + "=" * 60)
    print(f"  Classifier Accuracy : {acc * 100:.2f}%")
    print(f"  Regression MAE      : {mae:.2f} ms")
    print(f"  Regression R²       : {r2:.4f}")
    print("=" * 60)
    print("\nAI Decisions:")
    for i, d in enumerate(decisions, 1):
        print(f"  [{i}] {d[:120]}…" if len(d) > 120 else f"  [{i}] {d}")

    save_insights(INSIGHTS_PATH, acc, report, cm, fi, tree_rules, mae, r2, coefs, decisions)

    with open(MODEL_PKL_PATH, "wb") as f:
        pickle.dump(clf, f)
    print(f"[+] Decision Tree saved → {MODEL_PKL_PATH}")

    with open(REGRESSION_PKL_PATH, "wb") as f:
        pickle.dump(reg, f)
    print(f"[+] Linear Regression saved → {REGRESSION_PKL_PATH}")

    print("\n[✓] Training complete.")

if __name__ == "__main__":
    main()
