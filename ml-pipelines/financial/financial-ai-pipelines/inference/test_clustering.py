import joblib
import pandas as pd
import os

def main():
    print("--- Testing Clustering (Customer Segmentation) Model ---")
    weights_path = os.path.join(os.path.dirname(__file__), '..', 'weights', 'customer_segmentation_model.joblib')
    
    if not os.path.exists(weights_path):
        print(f"Error: Model not found at {weights_path}. Run training script first.")
        return
        
    print("Loading model pipeline...")
    pipeline = joblib.load(weights_path)
    
    # Dummy raw data (annual_income, spending_score)
    dummy_data = pd.DataFrame([
        {'annual_income': 120, 'spending_score': 90}, # High income, high spend
        {'annual_income': 40, 'spending_score': 20}   # Low income, low spend
    ])
    
    print("\nRunning Inference on raw inputs:")
    print(dummy_data)
    
    predictions = pipeline.predict(dummy_data)
    
    for i, cluster in enumerate(predictions):
        print(f"Sample {i+1} assigned to Customer Segment (Cluster): {cluster}")

if __name__ == "__main__":
    main()
