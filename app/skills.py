"""Skill and job-title vocabulary used for local, deterministic matching."""
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
}

TITLES = [
    "software engineer", "senior software engineer", "software developer", "backend developer",
    "backend engineer", "frontend developer", "frontend engineer", "full stack developer", "full stack engineer",
    "data scientist", "data engineer", "data analyst", "machine learning engineer", "devops engineer",
    "site reliability engineer", "cloud engineer", "cloud architect", "solutions architect", "qa engineer",
    "test engineer", "product manager", "project manager", "business analyst", "consultant", "sap consultant",
    "security engineer", "network engineer", "mobile developer", "android developer", "ios developer",
    "engineering manager", "technical lead", "tech lead", "architect", "developer", "engineer", "analyst",
]

_PATTERNS = {}
for canon, aliases in SKILLS.items():
    terms = [canon.lower()] + aliases
    alt = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    # skill tokens must not be glued to other word characters (handles C++, C#, Node.js)
    _PATTERNS[canon] = re.compile(r"(?<![\w+#.])(?:" + alt + r")(?![\w+#])", re.I)
# single-letter "R" and "Go" are too ambiguous in prose: require them upper/title-case standalone
_PATTERNS["R"] = re.compile(r"(?<![\w+#.])R(?![\w+#'])")
_PATTERNS["Go"] = re.compile(r"(?<![\w+#.])(?:Go|Golang|golang)(?![\w+#'])")


def find_skills(text: str):
    return [canon for canon, pat in _PATTERNS.items() if pat.search(text or "")]


def canonical_skill(name: str) -> str:
    n = (name or "").strip()
    for canon, aliases in SKILLS.items():
        if n.lower() == canon.lower() or n.lower() in aliases:
            return canon
    return n
