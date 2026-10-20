# AI Media Agent

An AI-powered agent built with Google's Agentic Development Kit (ADK) and Gemini models that automatically transforms blog posts into professional infographics.

The application features a robust FastAPI backend combined with a beautiful, modern Vanilla JavaScript frontend styled with glassmorphism.

## 📂 Project Structure

```text
media_agent/
│
├── main.py                # FastAPI server entry point (serves API and Frontend)
├── requirements.txt       # Project dependencies
│
├── backend/
│   ├── agent.py           # Core ADK agent logic
│   └── static/            # Generated infographics are stored here
│
└── frontend/
    ├── index.html         # Frontend interface
    ├── style.css          # Styling, dark mode, glassmorphism
    └── script.js          # API interactions
```

## 🚀 Setup & Installation

1. **Install Dependencies**
   Ensure you have Python installed, then install the required packages:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Credentials**
   The agent requires access to Google Cloud Vertex AI / Gemini. 
   - Fetch your Service Account Key `.json` file from your Google Cloud Console.
   - Place the `.json` file in a secure location on your machine (e.g. your project root).
   - Update your environment variables or the authentication logic to reference this `.json` file.

3. **Start the Server**
   The application uses a single FastAPI server to handle both the API endpoints and serve the frontend UI.
   ```bash
   uvicorn main:app --reload
   ```

4. **Access the Application**
   Open your browser and navigate to:
   [http://localhost:8000](http://localhost:8000)

## 💡 How it works
- Enter a blog URL in the web interface.
- The `backend/agent.py` fetches the text content, formulates a prompt, and generates an infographic using Gemini.
- The generated image is saved locally and served back to the UI for viewing and downloading.
