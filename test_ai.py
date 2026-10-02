from services.ai_service import analyze_technology


technology = {
    "full_name": "kubernetes/kubernetes",
    "description": "Production-grade container orchestration system",
    "language": "Go",
    "topics": [
        "containers",
        "kubernetes",
        "cloud"
    ],
    "stars": 100000
}


result = analyze_technology(technology)

print(result)