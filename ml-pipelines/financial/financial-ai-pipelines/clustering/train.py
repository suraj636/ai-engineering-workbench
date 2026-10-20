import numpy as np
import pandas as pd
from sklearn.datasets import make_blobs
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.cluster import KMeans, MiniBatchKMeans
from sklearn.metrics import silhouette_score, calinski_harabasz_score, davies_bouldin_score
import joblib
import os
import json

def load_data():
    print("Generating synthetic customer spending data...")
    X_raw, _ = make_blobs(n_samples=500, centers=4, cluster_std=1.5, random_state=42)
    # Make it look like real money and scores
    X_raw[:, 0] = np.abs(X_raw[:, 0] * 15 + 50) # Income
    X_raw[:, 1] = np.clip(np.abs(X_raw[:, 1] * 10 + 30), 1, 100) # Spending Score
    X = pd.DataFrame(X_raw, columns=['annual_income', 'spending_score'])
    return X

def exploratory_data_analysis(X):
    print("\n--- Exploratory Data Analysis ---")
    print(f"Dataset Shape: {X.shape}")
    print(f"Features: {X.columns.tolist()}")
    print("Summary Statistics:")
    print(X.describe().to_string())

def data_preprocessing():
    print("\n--- Data Preprocessing ---")
    print("Applying StandardScaler to normalize distances for clustering algorithms.")
    return StandardScaler()

def evaluate_baselines(X, preprocessor):
    print("\n--- Model Selection (Baselines) ---")
    X_scaled = preprocessor.fit_transform(X)
    baselines = {
        "K-Means": KMeans(n_clusters=4, random_state=42, n_init='auto'),
        "MiniBatch K-Means": MiniBatchKMeans(n_clusters=4, random_state=42, n_init='auto')
    }
    for name, model in baselines.items():
        labels = model.fit_predict(X_scaled)
        print(f"{name} Silhouette Score: {silhouette_score(X_scaled, labels):.4f}")

def build_and_train_model(X, preprocessor):
    print("\n--- Training Production Model ---")
    prod_model = KMeans(n_clusters=4, random_state=42, n_init='auto')
    prod_pipeline = Pipeline(steps=[('preprocessor', preprocessor), ('clusterer', prod_model)])
    prod_pipeline.fit(X)
    return prod_pipeline, prod_model

def validate_model(pipeline, X):
    print("\n--- Validation & Metrics ---")
    labels = pipeline.predict(X)
    X_transformed = pipeline.named_steps['preprocessor'].transform(X)
    
    metrics_dict = {
        "silhouette_score": silhouette_score(X_transformed, labels),
        "calinski_harabasz_score": calinski_harabasz_score(X_transformed, labels),
        "davies_bouldin_score": davies_bouldin_score(X_transformed, labels)
    }
    
    for k, v in metrics_dict.items():
        print(f"{k}: {v:.4f}")
    return metrics_dict

def save_artifacts(pipeline, model, metrics_dict):
    weights_dir = os.path.join(os.path.dirname(__file__), '..', 'weights')
    os.makedirs(weights_dir, exist_ok=True)
    
    model_path = os.path.join(weights_dir, 'customer_segmentation_model.joblib')
    joblib.dump(pipeline, model_path)
    print(f"\nModel saved to {model_path}")

    metrics_output = {
        "dataset_description": "Synthetically generated dataset for customer segmentation.",
        "model": "KMeans",
        "parameters": model.get_params(),
        "metrics": metrics_dict
    }
    metrics_path = os.path.join(os.path.dirname(__file__), 'metrics.json')
    with open(metrics_path, 'w') as f:
        json.dump(metrics_output, f, indent=4)
    print(f"Metrics saved to {metrics_path}")

def main():
    print("=== Clustering Pipeline Started ===")
    X = load_data()
    exploratory_data_analysis(X)
    
    preprocessor = data_preprocessing()
    evaluate_baselines(X, preprocessor)
    
    prod_pipeline, raw_model = build_and_train_model(X, preprocessor)
    metrics = validate_model(prod_pipeline, X)
    
    save_artifacts(prod_pipeline, raw_model, metrics)
    print("=== Pipeline Complete ===")

if __name__ == "__main__":
    main()
