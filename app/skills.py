"""Skill and job-title vocabulary used for local, deterministic matching."""
import functools
import re

# canonical name -> extra aliases (lower-case)
SKILLS = {
    "Python": [], "Java": [], "JavaScript": ["js"], "TypeScript": [], "Go": ["golang"], "C++": ["cpp"],
    "C#": ["csharp", ".net"], "Ruby": [], "PHP": [], "Rust": [], "Kotlin": [], "Swift": [], "Scala": [],
    "R": [], "SQL": ["mysql", "postgresql", "postgres", "t-sql"], "NoSQL": ["mongodb", "dynamodb", "cassandra"],
    "AWS": ["amazon web services"], "Azure": ["microsoft azure"], "GCP": ["google cloud"],
    "Docker": [], "Kubernetes": ["k8s"], "Terraform": [], "Linux": [], "Git": [], "CI/CD": ["jenkins", "github actions"],
    "React": ["react.js", "reactjs"], "Angular": [], "Vue": ["vue.js"], "Node.js": ["nodejs", "node"],
    "Django": [], "Flask": [], "FastAPI": [], "Spring": ["spring boot"], "REST": ["restful"], "GraphQL": [],
    "Microservices": [], "Kafka": [], "Spark": ["pyspark"], "Hadoop": [], "Airflow": [], "Snowflake": [],
    "Machine Learning": ["ml"], "Deep Learning": [], "TensorFlow": [], "PyTorch": [], "NLP": [],
    "Data Analysis": ["data analytics"], "Pandas": [], "Tableau": [], "Power BI": ["powerbi"], "Excel": [],
    "SAP": ["sap abap", "sap hana"], "Salesforce": [], "ServiceNow": [], "Agile": ["scrum"], "Jira": [],
    "Selenium": [], "Testing": ["qa", "test automation"], "Security": ["cybersecurity"], "Networking": [],
    "HTML": [], "CSS": [], "Figma": [], "Project Management": ["pmp"], "Communication": [],
    # AI / GenAI
    "Artificial Intelligence": ["ai", "a.i."], "Generative AI": ["genai", "gen ai", "gen-ai", "generative artificial intelligence"],
    "LLM": ["llms", "large language model", "large language models"], "Prompt Engineering": ["prompt engineer", "prompting"],
    "AI Agents": ["ai agent", "agentic", "agentic ai", "autonomous agents", "multi-agent"],
    "RAG": ["retrieval augmented generation", "retrieval-augmented generation"], "MCP": ["model context protocol"],
    "LangChain": [], "OpenAI": ["chatgpt", "gpt-4", "azure openai"], "Hugging Face": ["huggingface"],
    "Vector Databases": ["vector database", "pinecone", "faiss", "weaviate", "chromadb"], "MLOps": ["llmops"],
    "AI Governance": ["responsible ai", "ai ethics"],
    # delivery / management
    "Automation": ["workflow automation", "process automation", "automation framework", "rpa"],
    "Release Management": ["release planning", "release engineering", "release readiness", "release manager"],
    "Program Management": ["programme management", "program manager", "programme manager", "pgm"],
    "Product Management": ["product manager", "product owner", "product roadmap"],
    "Stakeholder Management": ["stakeholder engagement", "stakeholder communication", "stakeholders"],
    "Cross-functional Leadership": ["cross-functional", "cross functional"],
    "Governance": ["governance framework"], "Process Improvement": ["continuous improvement"],
    "Executive Communication": ["executive stakeholders", "executive reporting"],
    "Solution Architecture": ["system architecture", "software architecture", "solution design", "enterprise architecture"],
    "Change Management": [], "Risk Management": [], "Dashboards": ["dashboard", "dashboarding"],
    "OAuth": ["oauth2", "oauth 2.0"], "Nginx": [], "Telecom": ["telecommunications", "5g", "oss/bss"],
}

TITLES = [
    "software engineer", "senior software engineer", "software developer", "backend developer",
    "backend engineer", "frontend developer", "frontend engineer", "full stack developer", "full stack engineer",
    "data scientist", "data engineer", "data analyst", "machine learning engineer", "devops engineer",
    "site reliability engineer", "cloud engineer", "cloud architect", "solutions architect", "qa engineer",
    "test engineer", "product manager", "project manager", "business analyst", "consultant", "sap consultant",
    "security engineer", "network engineer", "mobile developer", "android developer", "ios developer",
    "engineering manager", "technical lead", "tech lead", "architect", "developer", "engineer", "analyst",
    "software architect", "ai architect", "ai engineer", "ai product manager", "program manager",
    "technical program manager", "release manager", "automation architect", "automation engineer",
    "verification engineer", "security analyst", "product owner", "delivery manager",
]

_PATTERNS = {}
for canon, aliases in SKILLS.items():
    terms = [canon.lower()] + aliases
    alt = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    # skill tokens must not be glued to other word characters (handles C++, C#, Node.js)
    _PATTERNS[canon] = re.compile(r"(?<![\w+#.])(?:" + alt + r")(?![\w+#])", re.I)
# single-letter "R" and "Go" are too ambiguous in prose: require them upper/title-case standalone
_PATTERNS["R"] = re.compile(r"(?<![\w+#.])R(?![\w+#'])")
# "AI" must be upper-case and standalone ("said", "maintain" etc. are excluded by the boundaries anyway)
_PATTERNS["Artificial Intelligence"] = re.compile(r"(?<![\w+#.])(?:AI|A\.I\.|[Aa]rtificial [Ii]ntelligence)(?![\w+#])")
_PATTERNS["Excel"] = re.compile(r"(?<![\w+#.])(?:Excel|MS Excel|Microsoft Excel)(?![\w+#])")
_PATTERNS["Go"] = re.compile(r"(?<![\w+#.])(?:Go|Golang|golang)(?![\w+#'])")


def find_skills(text: str):
    return list(_find_skills(text or ""))


@functools.lru_cache(maxsize=4096)  # scoring re-detects skills on every ranking; the same text gives the same skills
def _find_skills(text: str):
    return tuple(canon for canon, pat in _PATTERNS.items() if pat.search(text))


def canonical_skill(name: str) -> str:
    n = (name or "").strip()
    for canon, aliases in SKILLS.items():
        if n.lower() == canon.lower() or n.lower() in aliases:
            return canon
    return n
