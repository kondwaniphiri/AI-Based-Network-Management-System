"""Train future degradation prediction; evaluate on explicitly held-out sessions."""
import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.metrics import classification_report, confusion_matrix, mean_absolute_error
from sklearn.pipeline import make_pipeline
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

from build_windows import FEATURES, SNMP_FEATURES


def split_sessions(df, test_runs):
    if not test_runs or not set(test_runs).issubset(set(df["run_id"])):
        raise ValueError("All requested test run IDs must exist")
    train = df[~df["run_id"].isin(test_runs)].copy()
    test = df[df["run_id"].isin(test_runs)].copy()
    if train.empty or test.empty:
        raise ValueError("Collect separate training and test sessions")
    if set(train["target"]) != {"normal", "degraded"}:
        raise ValueError("Training sessions must contain normal and degraded outcomes")
    return train, test


def evaluate(train, test, columns):
    columns = [c for c in columns if train[c].notna().any()]
    model = make_pipeline(SimpleImputer(strategy="median", add_indicator=True),
                          DecisionTreeClassifier(max_depth=6, min_samples_leaf=5,
                                                 class_weight="balanced", random_state=42))
    model.fit(train[columns], train["target"])
    pred = model.predict(test[columns])
    report = classification_report(test["target"], pred,
                                   labels=["normal", "degraded"],
                                   output_dict=True, zero_division=0)
    return model, columns, dict(classification=report,
        confusion_matrix=confusion_matrix(test["target"], pred,
                                          labels=["normal", "degraded"]).tolist())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", default="datasets/windows.csv")
    p.add_argument("--test-runs", nargs="+", required=True,
                   help="Choose complete held-out sessions before model tuning")
    p.add_argument("--output-dir", default="datasets/window_model")
    args = p.parse_args()
    df = pd.read_csv(args.input)
    if df.duplicated(["run_id", "source", "window"]).any():
        p.error("Duplicate run/source/window rows")
    for col in ["window_seconds", "rtt_limit_ms", "failure_limit_pct"]:
        if df[col].nunique() != 1:
            p.error(f"All sessions must use the same {col}")
    train, test = split_sessions(df, args.test_runs)
    model, columns, result = evaluate(train, test, FEATURES)
    # Persistence threshold baseline predicts next outcome from current symptoms.
    rule = np.where((test["rtt_ms"] > df["rtt_limit_ms"].iloc[0]) |
                    (test["probe_failure_pct"] >= df["failure_limit_pct"].iloc[0]),
                    "degraded", "normal")
    result["threshold_baseline"] = classification_report(
        test["target"], rule, labels=["normal", "degraded"],
        output_dict=True, zero_division=0)
    result["train_runs"] = sorted(train["run_id"].unique().tolist())
    result["test_runs"] = sorted(test["run_id"].unique().tolist())
    result["train_rows"], result["test_rows"] = len(train), len(test)
    result["prediction_horizon_seconds"] = float(df["window_seconds"].iloc[0])
    result["test_class_counts"] = test["target"].value_counts().to_dict()
    result["features"] = columns
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    metadata = {k: float(df[k].iloc[0]) for k in
                ["window_seconds", "rtt_limit_ms", "failure_limit_pct"]}
    with (out / "classifier.pkl").open("wb") as f:
        pickle.dump(dict(model=model, features=columns, metadata=metadata), f)
    # Matched rows and split for the SNMP ablation; missing counters are imputed
    # using training data only. Never silently replace the base model.
    if all(c in df for c in SNMP_FEATURES) and train[SNMP_FEATURES].notna().any().any():
        snmp_model, snmp_cols, snmp_result = evaluate(train, test, FEATURES + SNMP_FEATURES)
        result["with_snmp"] = snmp_result
        result["with_snmp"]["features"] = snmp_cols
        with (out / "classifier_snmp.pkl").open("wb") as f:
            pickle.dump(dict(model=snmp_model, features=snmp_cols, metadata=metadata), f)
    # Optional next-window RTT predictor: actual successful HTTP RTT only.
    tr, te = train.dropna(subset=["next_rtt_ms"]), test.dropna(subset=["next_rtt_ms"])
    if len(tr) >= 20 and not te.empty:
        reg = make_pipeline(SimpleImputer(strategy="median", add_indicator=True),
                            DecisionTreeRegressor(max_depth=6, min_samples_leaf=5,
                                                  random_state=42))
        reg.fit(tr[columns], tr["next_rtt_ms"])
        result["next_rtt_mae_ms"] = mean_absolute_error(te["next_rtt_ms"], reg.predict(te[columns]))
        with (out / "rtt_regressor.pkl").open("wb") as f:
            pickle.dump(dict(model=reg, features=columns, metadata=metadata), f)
    (out / "evaluation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
