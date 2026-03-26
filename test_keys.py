import os
import urllib.request
import json

# Parse .env file manually
env_file = "/home/agents/.env"
keys = {}
with open(env_file, "r") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            parts = line.split("=", 1)
            if len(parts) == 2:
                keys[parts[0]] = parts[1].strip().strip("'").strip('"')

gemini_key = keys.get("GEMINI_API_KEY")
vertex_key = keys.get("VERTEX_API_KEY")
vertex_ci_key = keys.get("VERTEX_API_KEY_CI")
project_id = keys.get("GOOGLE_PROJECT_ID", "ci-agents")

def test_gemini():
    print("Testing Gemini API Key...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
    data = json.dumps({
        "contents": [{"parts":[{"text": "Hello, this is a test!"}]}]
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print("✅ Gemini API Key is VALID!")
                preview = json.loads(response.read().decode())
                if "candidates" in preview and len(preview["candidates"]) > 0:
                    print("   Response:", preview["candidates"][0]["content"]["parts"][0]["text"])
    except urllib.error.HTTPError as e:
        print(f"❌ Gemini API Key is INVALID! Status Code: {e.code}")
        print(f"   Response: {e.read().decode()}")
    except Exception as e:
        print(f"❌ Failed to connect to Gemini API: {e}")

def test_vertex(key_name, key_value):
    print(f"\nTesting {key_name} as an Access Token...")
    # Attempt 1: As a standard Bearer token (Access Token)
    url = f"https://us-central1-aiplatform.googleapis.com/v1/projects/{project_id}/locations/us-central1/publishers/google/models/gemini-1.5-flash-001:generateContent"
    data = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": "Hello world!"}]}]
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=data, headers={
        'Authorization': f'Bearer {key_value}',
        'Content-Type': 'application/json'
    })
    
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print(f"✅ {key_name} is VALID as Bearer Token!")
                preview = json.loads(response.read().decode())
                if "candidates" in preview and len(preview["candidates"]) > 0:
                    print("   Response:", preview["candidates"][0]["content"]["parts"][0]["text"])
                return
    except urllib.error.HTTPError as e:
        print(f"❌ {key_name} is INVALID as Bearer Token. Status Code: {e.code}")
        try:
            print(f"   Response: {json.loads(e.read().decode())}")
        except:
            print(f"   Error reading response body")
    except Exception as e:
        print(f"❌ Failed to connect to Vertex API: {e}")

    print(f"\nTesting {key_name} as a direct API Key parameter...")
    # Attempt 2: As an API Key (less common for Vertex, but possible)
    url_as_key = f"{url}?key={key_value}"
    req_no_auth = urllib.request.Request(url_as_key, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req_no_auth) as response:
            if response.status == 200:
                print(f"✅ {key_name} is VALID as an API Key parameter!")
                preview = json.loads(response.read().decode())
                if "candidates" in preview and len(preview["candidates"]) > 0:
                    print("   Response:", preview["candidates"][0]["content"]["parts"][0]["text"])
    except urllib.error.HTTPError as e:
        print(f"❌ {key_name} is INVALID as an API Key parameter. Status Code: {e.code}")
        try:
            print(f"   Response: {json.loads(e.read().decode())}")
        except:
             print(f"   Error reading response body")
    except Exception as e:
        print(f"❌ Failed to connect to Vertex API with key query param: {e}")


if __name__ == "__main__":
    test_gemini()
    if vertex_key:
        test_vertex("VERTEX_API_KEY", vertex_key)
    if vertex_ci_key:
        test_vertex("VERTEX_API_KEY_CI", vertex_ci_key)
