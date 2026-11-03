# Public demo on Hugging Face Spaces. Runs without a Gemini key: it plays back the saved real Gemini run.
FROM python:3.12-slim

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .
# Fills the Document tracking page from the sample inbox, using saved answers only.
RUN python run.py --mode replay --fresh

EXPOSE 7860
CMD ["uvicorn", "web_app:app", "--host", "0.0.0.0", "--port", "7860"]
