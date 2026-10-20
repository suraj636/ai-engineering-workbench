import joblib
import pandas as pd
import os

def main():
    print("--- Testing Regression (Credit Score) Model ---")
    weights_path = os.path.join(os.path.dirname(__file__), '..', 'weights', 'credit_score_model.joblib')
    
    if not os.path.exists(weights_path):
        print(f"Error: Model not found at {weights_path}. Run training script first.")
        return
        
    print("Loading model pipeline...")
    pipeline = joblib.load(weights_path)
    
    # Dummy raw data (income, existing_debt, years_employed, late_payments)
    dummy_data = pd.DataFrame([
        {'income': 80000, 'existing_debt': 5000, 'years_employed': 10, 'late_payments': 0}, # Likely High Score
        {'income': 40000, 'existing_debt': 30000, 'years_employed': 2, 'late_payments': 3}  # Likely Low Score
    ])
    
    print("\nRunning Inference on raw inputs:")
    print(dummy_data)
    
    predictions = pipeline.predict(dummy_data)
    
    for i, pred in enumerate(predictions):
        print(f"Sample {i+1} Predicted Credit Score: {pred:.0f}")

if __name__ == "__main__":
    main()
