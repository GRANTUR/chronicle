import logging
import json
import httpx

import config

log = logging.getLogger("chronicle.voice")


def transcribe_audio(audio_data: bytes, content_type: str = "audio/ogg") -> str | None:
    """Transcribe audio using Google Cloud Speech-to-Text REST API with API key."""
    # Use the REST API with the OAuth access token from our Google credentials
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        with open(config.GOOGLE_TOKEN_FILE) as f:
            token_data = json.load(f)

        creds = Credentials.from_authorized_user_info(token_data)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed token
            from pathlib import Path
            Path(config.GOOGLE_TOKEN_FILE).parent.mkdir(parents=True, exist_ok=True)
            with open(config.GOOGLE_TOKEN_FILE, "w") as f:
                json.dump(json.loads(creds.to_json()), f)

        access_token = creds.token
    except Exception as e:
        log.error(f"Failed to get Google access token: {e}")
        return None

    import base64
    audio_b64 = base64.b64encode(audio_data).decode("utf-8")

    # Map content types to encoding
    if "ogg" in content_type:
        encoding = "OGG_OPUS"
        sample_rate = 48000
    elif "webm" in content_type:
        encoding = "WEBM_OPUS"
        sample_rate = 48000
    else:
        encoding = "OGG_OPUS"
        sample_rate = 48000

    body = {
        "config": {
            "encoding": encoding,
            "sampleRateHertz": sample_rate,
            "languageCode": "en-US",
            "enableAutomaticPunctuation": True,
            "model": "latest_long",
        },
        "audio": {
            "content": audio_b64,
        },
    }

    try:
        resp = httpx.post(
            "https://speech.googleapis.com/v1/speech:recognize",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            log.warning("Speech-to-text returned no results")
            return None

        transcript = " ".join(
            r["alternatives"][0]["transcript"]
            for r in results
            if r.get("alternatives")
        ).strip()

        log.info(f"Transcribed: {transcript[:100]}")
        return transcript

    except Exception as e:
        log.error(f"Speech-to-text failed: {e}")
        return None
