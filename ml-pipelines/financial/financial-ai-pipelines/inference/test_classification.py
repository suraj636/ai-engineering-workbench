import joblib
import pandas as pd
import os

def main():
    print("--- Testing Classification (Credit Risk) Model ---")
    weights_path = os.path.join(os.path.dirname(__file__), '..', 'weights', 'credit_risk_model.joblib')
    
    if not os.path.exists(weights_path):
        print(f"Error: Model not found at {weights_path}. Run training script first.")
        return
        
    print("Loading model pipeline...")
    pipeline = joblib.load(weights_path)
    
    # Dummy raw data matching credit-g features
    # Since credit-g has many features, we'll create a small dummy DataFrame mimicking a single row
    # In a real scenario, this data would come from an API request.
    from sklearn.datasets import fetch_openml
    print("Fetching sample data for inference...")
    data = fetch_openml('credit-g', version=1, as_frame=True, parser='auto')
    
    # Take 10 sample rows
    sample_df = data.data.iloc[0:10]
    actual_targets = data.target.iloc[0:10].map({'good': 1, 'bad': 0})
    
    print("\nRunning Inference on 10 samples...")
    predictions = pipeline.predict(sample_df)
    
    for i in range(10):
        # Extract a few key features for display
        duration = sample_df.iloc[i]['duration']
        amount = sample_df.iloc[i]['credit_amount']
        age = sample_df.iloc[i]['age']
        purpose = sample_df.iloc[i]['purpose']
        
        pred_label = "Good Risk" if predictions[i] == 1 else "Bad Risk"
        actual_label = "Good Risk" if actual_targets.iloc[i] == 1 else "Bad Risk"
        match_icon = "✅" if pred_label == actual_label else "❌"
        
        print(f"--- Sample {i+1} ---")
        print(f"Inputs   : Duration={duration}m, Amount={amount}, Age={age}, Purpose={purpose}")
        print(f"Actual   : {actual_label}")
        print(f"Predicted: {pred_label} {match_icon}\n")

if __name__ == "__main__":
    main()
