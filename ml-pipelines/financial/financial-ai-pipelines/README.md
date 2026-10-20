# Fintech ML Models Sandbox

This repository contains three self-contained machine learning pipelines designed for a fintech context. It demonstrates end-to-end MLOps best practices including data preprocessing, model selection, baseline evaluation, and production model training using ensemble techniques (Bagging/Boosting).

## 🚀 Architecture & Process Flow
For all models, the following architectural workflow is implemented using `scikit-learn`:
1. **Data Preprocessing**: Raw data is passed through a `ColumnTransformer` (applying `StandardScaler` for numerical data and `OneHotEncoder` for categorical data). This is bundled into a `Pipeline`.
2. **Baseline Evaluation**: Simple models (like Logistic Regression, Decision Trees, or Ridge) are trained first to establish a performance baseline.
3. **Production Model Training**: A more complex ensemble model (Gradient Boosting or Random Forest) is trained.
4. **Serialization**: The entire `Pipeline` (preprocessing + model) is saved to disk as a `.joblib` file in the `weights/` directory, alongside a `metrics.json` file detailing the model's parameters and performance.
5. **Inference**: The testing scripts load the `Pipeline` and pass *raw* input data directly into it, allowing the pipeline to handle scaling/encoding automatically during inference.

---

## 🧠 Models & Use Cases

### 1. Classification (Credit Risk Prediction)
* **Goal**: Predict whether a loan applicant is safe to lend to or likely to default.
* **Dataset**: OpenML `credit-g` (German Credit Data). Contains 20 financial and personal features (e.g., loan duration, credit amount, age, loan purpose).
* **Final Model**: Gradient Boosting Classifier
* **Key Metrics**:
  * Accuracy: ~79.0%
  * F1-Score: ~0.85
  * ROC AUC: ~0.81

### 2. Regression (Credit Score Prediction)
* **Goal**: Predict a continuous numerical credit score (300-850) based on a user's financial profile.
* **Dataset**: Synthetically generated fintech data with 4 key features: `income`, `existing_debt`, `years_employed`, and `late_payments`.
* **Final Model**: Random Forest Regressor (Bagging)
* **Key Metrics**:
  * R² Score: ~0.947 (explains ~94.7% of variance)
  * RMSE: ~11.76
  * MAE: ~8.49

### 3. Clustering (Customer Segmentation)
* **Goal**: Unsupervised grouping of users into distinct "personas" based on spending habits for targeted credit card marketing.
* **Dataset**: Synthetically generated clusters (`make_blobs`) representing `annual_income` and `spending_score` (1-100).
* **Final Model**: K-Means Clustering (k=4 segments)
* **Key Metrics**:
  * Silhouette Score: ~0.565
  * Calinski-Harabasz Index: ~927.7
  * Davies-Bouldin Index: ~0.605

---

## 📁 Repository Structure

- `classification/`: Credit Risk training script and metrics.
- `regression/`: Credit Score training script and metrics.
- `clustering/`: Customer Segmentation training script and metrics.
- `inference/`: Scripts to test the trained pipelines by feeding them raw data samples.
- `weights/`: Directory where the trained `.joblib` model pipelines are saved.
- `main.py`: Interactive CLI tool to easily train models or run inference.

---

## ⚙️ Setup & Execution

1. **Install dependencies**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Run the Interactive CLI**:
   The easiest way to explore the repository is using the built-in CLI manager:
   ```bash
   python main.py
   ```
   *This will launch an interactive menu allowing you to quickly retrain any model or run its inference test script.*
