"""All settings in one place. Change values here, not in the services."""
import os

from dotenv import load_dotenv

load_dotenv()

RINGS = ["Adopt", "Trial", "Assess", "Hold"]
QUADRANTS = ["Techniques", "Tools", "Platforms", "Languages & Frameworks"]

# Defaults for the sidebar. Office server Ollama: http://192.168.45.161:11434
DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
SUGGESTED_MODELS = ["llama3.2:3b", "qwen3:4b", "qwen3:8b", "gemma3:4b"]

DEFAULT_DAYS = 30          # only signals from the last N days (GitHub: repos created in that window)
DEFAULT_MAX_SIGNALS = 40   # signals sent to the LLM for extraction
DEFAULT_TOP_N = 12         # technologies that get a ring
BATCH_SIZE = 8             # signals per extraction call

SOURCES = ["GitHub", "Y Combinator", "Hacker News", "RSS feeds"]

# Edit this: it tells the LLM what "relevant for bbv" means.
BBV_CONTEXT = (
    "bbv is a Swiss software and consulting company. It builds custom software, cloud and web "
    "platforms, mobile apps, embedded and IoT systems, and data and AI solutions, and does agile "
    "and architecture consulting. Customers are in industry and manufacturing, medtech, finance "
    "and insurance, mobility and the public sector in Switzerland and Europe."
)

# Keyword filters per technology area (taken from the original app, plus "All").
AREAS = {
    "All": {"github": "", "keywords": []},
    "AI / LLM": {"github": "ai OR llm OR agent",
                 "keywords": ["ai", "artificial intelligence", "llm", "agent", "generative ai", "machine learning"]},
    "Cloud": {"github": "cloud OR kubernetes OR serverless",
              "keywords": ["cloud", "kubernetes", "serverless", "infrastructure", "compute"]},
    "DevOps": {"github": "devops OR ci-cd OR infrastructure",
               "keywords": ["devops", "deployment", "ci/cd", "observability", "infrastructure"]},
    "Cybersecurity": {"github": "cybersecurity OR security",
                      "keywords": ["security", "cybersecurity", "vulnerability", "authentication", "identity"]},
    "Data": {"github": "data-engineering OR database OR analytics",
             "keywords": ["data", "database", "analytics", "data engineering", "pipeline"]},
    "Developer Tools": {"github": "developer-tools OR coding-agent",
                        "keywords": ["developer tools", "developer", "coding", "api", "sdk", "debugging"]},
}

RSS_FEEDS = [
    "https://feed.infoq.com/",
    "https://thenewstack.io/feed/",
    "https://martinfowler.com/feed.atom",
    "https://lobste.rs/rss",
    "https://github.blog/feed/",
    "https://www.cncf.io/feed/",
    "https://aws.amazon.com/blogs/aws/feed/",
    "https://devblogs.microsoft.com/dotnet/feed/",
    "https://spring.io/blog.atom",
    "https://simonwillison.net/atom/everything/",
    "https://huggingface.co/blog/feed.xml",
    "https://interrupt.memfault.com/feed.xml",
]

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "runs")
