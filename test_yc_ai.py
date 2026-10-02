from services.yc_service import get_yc_companies
from services.ai_service import analyze_technology


companies = get_yc_companies(
    limit=1,
    technology_area="AI / LLM"
)

company = companies[0]

print("YC COMPANY:")
print(company)

print("\nAI ANALYSIS:")

analysis = analyze_technology(company)

print(analysis)