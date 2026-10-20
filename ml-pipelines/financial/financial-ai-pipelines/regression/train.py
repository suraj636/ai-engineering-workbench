import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.linear_model import Ridge, Lasso
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
import joblib
import os
import json

def load_data():
    print("Generating synthetic fintech data...")
    n_samples = 1000
    np.random.seed(42)
    income = np.random.normal(60000, 20000, n_samples)
    existing_debt = np.random.normal(15000, 10000, n_samples)
    years_employed = np.random.randint(0, 20, n_samples)
    late_payments = np.random.poisson(1, n_samples)
    
    credit_score = 650 + (income / 1000) * 1.5 - (existing_debt / 1000) * 2 + (years_employed * 5) - (late_payments * 25)
    credit_score = np.clip(credit_score, 300, 850)
    
    X = pd.DataFrame({
        'income': income,
        'existing_debt': existing_debt,
        'years_employed': years_employed,
        'late_payments': late_payments
    })
    return X, credit_score

def exploratory_data_analysis(X, y):
    print("\n--- Exploratory Data Analysis ---")
    print(f"Dataset Shape: {X.shape}")
    print(f"Target Feature (Credit Score) Stats:\n  Min: {np.min(y):.0f}, Max: {np.max(y):.0f}, Mean: {np.mean(y):.0f}")
    print(f"Features preview:\n{X.head(2).to_string()}")

def data_preprocessing():
    print("\n--- Data Preprocessing ---")
    print("Applying StandardScaler to normalize continuous numerical features.")
    return StandardScaler()

def evaluate_baselines(X_train, X_test, y_train, y_test, preprocessor):
    print("\n--- Model Selection (Baselines) ---")
    baselines = {
        "Ridge Regression": Ridge(),
        "Lasso Regression": Lasso()
    }
    for name, model in baselines.items():
        pipeline = Pipeline(steps=[('preprocessor', preprocessor), ('regressor', model)])
        pipeline.fit(X_train, y_train)
        preds = pipeline.predict(X_test)
        print(f"{name} -> R2: {r2_score(y_test, preds):.4f}")

def build_and_train_model(X_train, y_train, preprocessor):
    print("\n--- Training Production Model ---")
    prod_model = RandomForestRegressor(n_estimators=100, random_state=42)
    prod_pipeline = Pipeline(steps=[('preprocessor', preprocessor), ('regressor', prod_model)])
    prod_pipeline.fit(X_train, y_train)
    return prod_pipeline, prod_model

def validate_model(pipeline, X_test, y_test):
    print("\n--- Validation & Metrics ---")
    preds = pipeline.predict(X_test)
    
    metrics_dict = {
        "rmse": np.sqrt(mean_squared_error(y_test, preds)),
        "mse": mean_squared_error(y_test, preds),
        "mae": mean_absolute_error(y_test, preds),
        "r2": r2_score(y_test, preds)
    }
    
    for k, v in metrics_dict.items():
        print(f"{k.upper()}: {v:.4f}")
    return metrics_dict

def save_artifacts(pipeline, model, metrics_dict):
    weights_dir = os.path.join(os.path.dirname(__file__), '..', 'weights')
    os.makedirs(weights_dir, exist_ok=True)
    
    model_path = os.path.join(weights_dir, 'credit_score_model.joblib')
    joblib.dump(pipeline, model_path)
    print(f"\nModel saved to {model_path}")

    metrics_output = {
        "dataset_description": "Synthetically generated fintech dataset simulating financial profiles.",
        "model": "RandomForestRegressor",
        "parameters": model.get_params(),
        "metrics": metrics_dict
    }
    metrics_path = os.path.join(os.path.dirname(__file__), 'metrics.json')
    with open(metrics_path, 'w') as f:
        json.dump(metrics_output, f, indent=4)
    print(f"Metrics saved to {metrics_path}")

def main():
    print("=== Regression Pipeline Started ===")
    X, y = load_data()
    exploratory_data_analysis(X, y)
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    preprocessor = data_preprocessing()
    evaluate_baselines(X_train, X_test, y_train, y_test, preprocessor)
    
    prod_pipeline, raw_model = build_and_train_model(X_train, y_train, preprocessor)
    metrics = validate_model(prod_pipeline, X_test, y_test)
    
    save_artifacts(prod_pipeline, raw_model, metrics)
    print("=== Pipeline Complete ===")

if __name__ == "__main__":
    main()
