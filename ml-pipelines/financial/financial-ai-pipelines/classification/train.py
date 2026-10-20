import pandas as pd
from sklearn.datasets import fetch_openml
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import accuracy_score, classification_report, precision_score, recall_score, f1_score, roc_auc_score
import joblib
import os
import json

def load_data():
    print("Fetching 'credit-g' dataset from OpenML...")
    data = fetch_openml('credit-g', version=1, as_frame=True, parser='auto')
    X, y = data.data, data.target
    # Target is 'good' or 'bad'. Convert to binary: 'good' -> 1, 'bad' -> 0
    y = y.map({'good': 1, 'bad': 0})
    return X, y

def exploratory_data_analysis(X, y):
    print("\n--- Exploratory Data Analysis ---")
    print(f"Dataset Shape: {X.shape}")
    print(f"Target Distribution:\n{y.value_counts(normalize=True).to_string()}")
    print(f"Missing Values: {X.isnull().sum().max()} (Max missing in any column)")

def data_preprocessing(X):
    print("\n--- Data Preprocessing ---")
    numeric_features = X.select_dtypes(include=['int64', 'float64']).columns
    categorical_features = X.select_dtypes(include=['category', 'object']).columns
    
    print(f"Identified {len(numeric_features)} numeric and {len(categorical_features)} categorical features.")
    
    numeric_transformer = StandardScaler()
    categorical_transformer = OneHotEncoder(handle_unknown='ignore')
    
    preprocessor = ColumnTransformer(
        transformers=[
            ('num', numeric_transformer, numeric_features),
            ('cat', categorical_transformer, categorical_features)
        ])
    return preprocessor

def evaluate_baselines(X_train, X_test, y_train, y_test, preprocessor):
    print("\n--- Model Selection (Baselines) ---")
    baselines = {
        "Logistic Regression": LogisticRegression(max_iter=1000),
        "Decision Tree": DecisionTreeClassifier(random_state=42)
    }
    
    for name, model in baselines.items():
        pipeline = Pipeline(steps=[('preprocessor', preprocessor), ('classifier', model)])
        pipeline.fit(X_train, y_train)
        preds = pipeline.predict(X_test)
        print(f"{name} Accuracy: {accuracy_score(y_test, preds):.4f}")

def build_and_train_model(X_train, y_train, preprocessor):
    print("\n--- Training Production Model ---")
    prod_model = GradientBoostingClassifier(n_estimators=100, random_state=42)
    prod_pipeline = Pipeline(steps=[('preprocessor', preprocessor), ('classifier', prod_model)])
    prod_pipeline.fit(X_train, y_train)
    return prod_pipeline, prod_model

def validate_model(pipeline, X_test, y_test):
    print("\n--- Validation & Metrics ---")
    preds = pipeline.predict(X_test)
    probs = pipeline.predict_proba(X_test)[:, 1]
    
    metrics_dict = {
        "accuracy": accuracy_score(y_test, preds),
        "precision": precision_score(y_test, preds),
        "recall": recall_score(y_test, preds),
        "f1_score": f1_score(y_test, preds),
        "roc_auc": roc_auc_score(y_test, probs)
    }
    
    for k, v in metrics_dict.items():
        print(f"{k.capitalize()}: {v:.4f}")
        
    print("\nClassification Report:\n", classification_report(y_test, preds))
    return metrics_dict

def save_artifacts(pipeline, model, metrics_dict):
    weights_dir = os.path.join(os.path.dirname(__file__), '..', 'weights')
    os.makedirs(weights_dir, exist_ok=True)
    
    # Save pipeline
    model_path = os.path.join(weights_dir, 'credit_risk_model.joblib')
    joblib.dump(pipeline, model_path)
    print(f"\nModel saved to {model_path}")
    
    # Save metrics
    metrics_output = {
        "dataset_description": "The 'credit-g' (German Credit Data) dataset from OpenML.",
        "model": "GradientBoostingClassifier",
        "parameters": model.get_params(),
        "metrics": metrics_dict
    }
    metrics_path = os.path.join(os.path.dirname(__file__), 'metrics.json')
    with open(metrics_path, 'w') as f:
        json.dump(metrics_output, f, indent=4)
    print(f"Metrics saved to {metrics_path}")

def main():
    print("=== Classification Pipeline Started ===")
    X, y = load_data()
    exploratory_data_analysis(X, y)
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    preprocessor = data_preprocessing(X)
    evaluate_baselines(X_train, X_test, y_train, y_test, preprocessor)
    
    prod_pipeline, raw_model = build_and_train_model(X_train, y_train, preprocessor)
    
    metrics = validate_model(prod_pipeline, X_test, y_test)
    
    save_artifacts(prod_pipeline, raw_model, metrics)
    print("=== Pipeline Complete ===")

if __name__ == "__main__":
    main()
