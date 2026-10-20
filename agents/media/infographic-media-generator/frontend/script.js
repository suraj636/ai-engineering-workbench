document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('generate-form');
    const urlInput = document.getElementById('url-input');
    const submitBtn = document.getElementById('submit-btn');
    const btnText = submitBtn.querySelector('.btn-text');
    const spinner = submitBtn.querySelector('.spinner');
    const statusMessage = document.getElementById('status-message');
    
    let statusInterval;
    const staticMessages = [
        "Fetching blog content...",
        "Analyzing text structure...",
        "Generating professional infographic...",
        "Evaluating image quality...",
        "Applying finishing touches..."
    ];
    
    const resultContainer = document.getElementById('result-container');
    const imageWrapper = document.getElementById('image-wrapper');
    const downloadBtn = document.getElementById('download-btn');
    
    const errorContainer = document.getElementById('error-container');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const url = urlInput.value.trim();
        if (!url) return;

        // Reset UI State
        setLoadingState(true);
        resultContainer.classList.add('hidden');
        errorContainer.classList.add('hidden');
        imageWrapper.innerHTML = '';
        downloadBtn.href = '#';

        try {
            const response = await fetch('/api/generate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ url: url })
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || 'An error occurred while generating the infographic.');
            }

            // Success
            showResult(data.image_url);
            
        } catch (error) {
            showError(error.message);
        } finally {
            setLoadingState(false);
        }
    });

    function setLoadingState(isLoading) {
        if (isLoading) {
            submitBtn.disabled = true;
            btnText.classList.add('hidden');
            spinner.classList.remove('hidden');
            
            // Start cycling through status messages
            statusMessage.classList.remove('hidden');
            statusMessage.textContent = "Initializing AI Agent...";
            let msgIndex = 0;
            statusInterval = setInterval(() => {
                statusMessage.textContent = staticMessages[msgIndex];
                msgIndex = (msgIndex + 1) % staticMessages.length;
            }, 3500); // Change message every 3.5 seconds
            
        } else {
            submitBtn.disabled = false;
            btnText.classList.remove('hidden');
            spinner.classList.add('hidden');
            
            // Stop status messages
            statusMessage.classList.add('hidden');
            clearInterval(statusInterval);
        }
    }

    function showResult(imageUrl) {
        // Create an image element to ensure it loads before showing
        const img = new Image();
        img.src = imageUrl;
        img.alt = 'Generated Infographic';
        
        imageWrapper.appendChild(img);
        
        downloadBtn.href = imageUrl;
        downloadBtn.classList.remove('hidden');
        
        resultContainer.classList.remove('hidden');
        
        // Smooth scroll to result
        resultContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function showError(message) {
        errorContainer.textContent = message;
        errorContainer.classList.remove('hidden');
    }
});
