import os
import glob
import json
import time
import unicodedata
from datetime import datetime
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
import streamlit as st
from google import genai
from google.genai import types
import toml

cbconfig = toml.load("cbconfig.toml")

USER_AVATAR = "👤"
ASSISTANT_AVATAR = "🤖"

HIDEMENU = """
<style>
.stApp [data-testid="stHeader"] {
    display:none;
}
p img{
    margin-bottom: 0.6rem;
}
[data-testid="stSidebarCollapseButton"] {
    display:none;
}
[data-testid="baseButton-headerNoPadding"] {
    display:none;
}
</style>
"""

BASE_URLS = [
    "https://www.ihu.gr/vivliothiki-kentro-pliroforisis/serres",
    "https://www.ihu.gr/vivliothiki-kentro-pliroforisis/sindos",
    "https://www.ihu.gr/vivliothiki-kentro-pliroforisis"
]

EXCLUDED_URL_PARTS = [
    "/en/", "/scholes", "/tmimata", "/dioikisi", "/prokiryxeis",
    "/diagonismoi", "/wp-login", "/wp-admin", "/feed"
]

WELCOME_MESSAGE = (
    "Καλώς ήρθατε στον ψηφιακό βοηθό των **Βιβλιοθηκών του ΔΙΠΑΕ**! 👋\n\n"
    "Μπορείτε να επιλέξετε ένα από τα παραπάνω θέματα ή να πληκτρολογήσετε την ερώτησή σας "
    "σχετικά με τα ωράρια λειτουργίας (Σίνδος, Σέρρες, Θέρμη), τον δανεισμό βιβλίων και τις υπηρεσίες."
)

def normalize_greek(text):
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(ch) != "Mn"
    )

@st.cache_resource(ttl="1d", show_spinner="Φόρτωση και ευρετηρίαση όλων των υπομενού των Βιβλιοθηκών ΔΙΠΑΕ...")
def fetch_all_library_pages_v3():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "el-GR,el;q=0.9,en;q=0.8",
    }
    visited = set()
    urls_to_crawl = list(BASE_URLS)
    
    # Εντοπισμός όλων των συνδέσμων από τα αριστερά μενού και το περιεχόμενο των σελίδων της βιβλιοθήκης
    for base_url in BASE_URLS:
        try:
            res = requests.get(base_url, headers=headers, timeout=8)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                for tag in soup(["header", "footer", "script", "style"]):
                    tag.extract()
                
                # Συλλογή συνδέσμων από το αριστερό μενού (aside/menu/widget) και το κύριο περιεχόμενο
                containers = soup.find_all(["aside", "main", "article"]) or [soup]
                for container in containers:
                    for a_tag in container.find_all("a", href=True):
                        href = a_tag["href"].strip()
                        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                            continue
                        full_url = urljoin(base_url, href).split("#")[0].rstrip("/")
                        if "ihu.gr" not in full_url:
                            continue
                        if full_url.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".zip", ".mp4", ".pdf")):
                            continue
                        if any(ex in full_url.lower() for ex in EXCLUDED_URL_PARTS):
                            continue
                        if full_url not in urls_to_crawl:
                            urls_to_crawl.append(full_url)
        except Exception:
            continue

    extracted_text = ""
    crawled_count = 0
    for url in urls_to_crawl[:35]:
        if url in visited:
            continue
        visited.add(url)
        try:
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                for tag in soup(["script", "style", "header", "footer"]):
                    tag.extract()
                body = soup.body or soup
                text = " ".join(body.stripped_strings)
                if text:
                    extracted_text += f"\n\n=== ΠΗΓΗ: {url} ===\n{text}"
                    crawled_count += 1
        except Exception:
            continue
            
    return extracted_text, crawled_count

@st.cache_resource(ttl="1d", show_spinner=False)
def get_available_models(_client):
    preferred = ["gemini-3.8-flash", "gemini-2.5-flash"]
    try:
        for m in _client.models.list():
            name = m.name.replace("models/", "")
            if "flash" in name and "image" not in name and "audio" not in name and name not in preferred:
                preferred.append(name)
    except Exception:
        pass
    return preferred

def find_logo():
    for path in ["static/DI.PA.E._logo.png", "DI.PA.E._logo.png"]:
        if os.path.exists(path):
            return path
    for folder in ["static", "."]:
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            for f in glob.glob(os.path.join(folder, ext)):
                if os.path.isfile(f) and not f.endswith("favicon.ico"):
                    return f
    return None

def log_anonymous_stat(source, category, question, answered):
    webhook_url = st.secrets.get("STATS_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return
    try:
        payload = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "category": category,
            "question": question,
            "answered": "Ναι" if answered else "Όχι"
        }
        requests.post(webhook_url, json=payload, timeout=10)
    except Exception:
        pass

def get_system_prompt():
    live_content, _ = fetch_all_library_pages_v3()
    return f"""
Είσαι ο επίσημος ψηφιακός βοηθός των Βιβλιοθηκών και Κέντρων Πληροφόρησης του Διεθνούς Πανεπιστημίου της Ελλάδος (ΔΙΠΑΕ).
Απαντάς πάντα στα ελληνικά με ευγένεια, σαφήνεια και απόλυτη ακρίβεια.

ΑΥΣΤΗΡΟΙ ΚΑΝΟΝΕΣ:
1. Οι Βιβλιοθήκες του ΔΙΠΑΕ λειτουργούν αποκλειστικά σε Σίνδο, Σέρρες και Θέρμη. Δεν λειτουργούν σε άλλες πόλεις.
2. Βασίσου ΑΠΟΚΛΕΙΣΤΙΚΑ στα παρακάτω επίσημα κείμενα που αντλήθηκαν από τις ιστοσελίδες και τα υπομενού (Ωράρια, Συχνές Ερωτήσεις, Κανονισμοί, Υπηρεσίες) των Βιβλιοθηκών του ΔΙΠΑΕ. Μην επινοείς κανόνες ή αριθμούς.
3. Όταν απαντάς, ανέφερε στο τέλος τη σχετική επίσημη σελίδα (URL) από τις πηγές παρακάτω ώστε ο χρήστης να μπορεί να τη δει αναλυτικά.
4. Αν μια πληροφορία δεν υπάρχει καθόλου στα παρακάτω κείμενα, δήλωσε ξεκάθαρα ότι δεν αναφέρεται αναλυτικά στις σελίδες των βιβλιοθηκών και πρότεινε στον χρήστη να επισκεφθεί την επίσημη ιστοσελίδα (https://www.ihu.gr/vivliothiki-kentro-pliroforisis) ή να επικοινωνήσει απευθείας με το προσωπικό της βιβλιοθήκης (αναφέροντας και τα σχετικά στοιχεία επικοινωνίας, όπως τηλέφωνα ή email, αν υπάρχουν στα παρακάτω κείμενα).

ΜΟΡΦΗ ΕΞΟΔΟΥ (ΑΥΣΤΗΡΑ JSON):
Επίστρεψε ΑΠΟΚΛΕΙΣΤΙΚΑ ένα έγκυρο αντικείμενο JSON με τα εξής 4 πεδία:
- "answer": Η αναλυτική απάντησή σου στα ελληνικά (με Markdown μορφοποίηση).
- "category": Μία από τις εξής κατηγορίες: "Ωράριο Λειτουργίας", "Δανεισμός & Κανόνες", "Παράρτημα Σίνδου", "Παράρτημα Σερρών", "Παράρτημα Θέρμης", "Ηλεκτρονικές Πηγές & Βάσεις", "Επικοινωνία & Προσωπικό", "Άλλο".
- "sanitized_question": Η ερώτηση του χρήστη όπως ακριβώς γράφτηκε, ΑΛΛΑ αν περιέχει ονοματεπώνυμο, Αριθμό Μητρώου, email ή τηλέφωνο, αντικατάστησέ τα με "[ΑΝΩΝΥΜΟ]" για συμμόρφωση με τον GDPR.
- "answered": true αν η απάντηση βρέθηκε στα κείμενα της βιβλιοθήκης, ή false αν η πληροφορία δεν υπήρχε και παρέπεμψες στο προσωπικό.

ΕΠΙΣΗΜΟ ΠΕΡΙΕΧΟΜΕΝΟ ΙΣΤΟΣΕΛΙΔΩΝ & ΥΠΟΜΕΝΟΥ ΔΙΠΑΕ:
{live_content}
"""

def generate_reply(user_query, source_label, client):
    models_to_try = get_available_models(client)
    full_instructions = get_system_prompt()
    
    answer = None
    last_error = ""
    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=user_query,
                config=types.GenerateContentConfig(
                    system_instruction=full_instructions,
                    temperature=0.0,
                    response_mime_type="application/json",
                )
            )
            if response and response.text:
                data = json.loads(response.text)
                answer = data.get("answer", "")
                category = data.get("category", "Άλλο")
                sanitized_q = data.get("sanitized_question", user_query)
                answered = bool(data.get("answered", True))
                
                log_anonymous_stat(source_label, category, sanitized_q, answered)
                break
        except Exception as e:
            last_error = str(e)
            if "503" in last_error or "UNAVAILABLE" in last_error:
                time.sleep(2)
            continue
    
    if not answer:
        answer = f"Προέκυψε προσωρινό σφάλμα επικοινωνίας με το AI ({last_error}). Παρακαλώ ξαναστείλτε την ερώτηση ή επικοινωνήστε με το προσωπικό της βιβλιοθήκης."
    return answer

if __name__ == "__main__":
    st.set_page_config(page_title="Βιβλιοθήκες ΔΙΠΑΕ - AI Chatbot", page_icon="🤖", initial_sidebar_state="expanded")
    st.markdown(HIDEMENU, unsafe_allow_html=True)
    
    client = genai.Client(
        api_key=st.secrets["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=15000)
    )
    
    if "current_qa" not in st.session_state:
        st.session_state.current_qa = None
    if "show_license" not in st.session_state:
        st.session_state.show_license = False
    
    # --- ΑΡΙΣΤΕΡΗ ΠΛΑΪΝΗ ΜΠΑΡΑ ---
    logo_path = find_logo()
    if logo_path:
        st.sidebar.image(logo_path, use_container_width=True)
        st.sidebar.markdown("\n")
        
    st.sidebar.markdown(cbconfig['side']['title'])
    st.sidebar.markdown(cbconfig['side']['intro'])
    st.sidebar.markdown("---")
    
    st.sidebar.link_button("🌐 Βιβλιοθήκες ΔΙΠΑΕ", "https://www.ihu.gr/vivliothiki-kentro-pliroforisis", use_container_width=True)
    
    if st.sidebar.button("⚖️ Αδειοδότηση", use_container_width=True):
        st.session_state.show_license = not st.session_state.show_license
        
    if st.session_state.show_license:
        _, indexed_pages = fetch_all_library_pages_v3()
        st.sidebar.info(
            "**Αδειοδότηση & Συμμόρφωση:**\n\n"
            "Προσαρμογή του ανοικτού λογισμικού *Kingbot (SJSU Library - MIT License)* "
            "για τις Βιβλιοθήκες του ΔΙ.ΠΑ.Ε., σύμφωνα με την ελληνική νομοθεσία "
            "(Ν. 2121/1993 & Ν. 4727/2020) και τον Κανονισμό Λειτουργίας του Ιδρύματος.\n\n"
            "**Προστασία Δεδομένων (GDPR):**\n"
            "Δεν συλλέγονται προσωπικά δεδομένα χρηστών (Καν. ΕΕ 2016/679 & Ν. 4624/2019). "
            "Καταγράφονται αποκλειστικά ανώνυμα στατιστικά θεματολογίας για τη βελτίωση των υπηρεσιών.\n\n"
            f"**Πηγές ΔΙ.ΠΑ.Ε.:** Ευρετηριάστηκαν {indexed_pages} επίσημες σελίδες."
        )
    
    # --- ΚΕΝΤΡΙΚΗ ΠΕΡΙΟΧΗ ---
    st.title("🏛️ Βιβλιοθήκες ΔΙΠΑΕ")
    st.markdown("\n")
    
    col21, col22, col23 = st.columns(3)
    with col21:
        button1 = st.button("📍 Παράρτημα Σίνδου", use_container_width=True)
    with col22:
        button2 = st.button("📍 Παράρτημα Σερρών", use_container_width=True)
    with col23:
        button3 = st.button("📚 Δανεισμός & FAQ", use_container_width=True)
        
    st.markdown("---")
    
    user_query = st.chat_input(placeholder="Ρωτήστε για ωράρια, δανεισμό, συχνές ερωτήσεις, Σίνδο ή Σέρρες...")
    
    new_query = None
    source_label = "Ελεύθερο κείμενο"
    if button1:
        new_query = "Ποιο είναι το ωράριο, τα τηλέφωνα επικοινωνίας και οι υπηρεσίες της βιβλιοθήκης στη Σίνδο;"
        source_label = "Κουμπί: Παράρτημα Σίνδου"
    elif button2:
        new_query = "Ποιο είναι το ωράριο, τα τηλέφωνα επικοινωνίας και οι υπηρεσίες της βιβλιοθήκης στις Σέρρες;"
        source_label = "Κουμπί: Παράρτημα Σερρών"
    elif button3:
        new_query = "Τι αναφέρουν οι συχνές ερωτήσεις και οι κανονισμοί σχετικά με τον δανεισμό στις βιβλιοθήκες του ΔΙΠΑΕ;"
        source_label = "Κουμπί: Δανεισμός & FAQ"
    elif user_query:
        new_query = user_query
        source_label = "Ελεύθερο κείμενο"
        
    if new_query:
        with st.spinner("Αναζήτηση στα δεδομένα των βιβλιοθηκών..."):
            reply = generate_reply(new_query, source_label, client)
        st.session_state.current_qa = {
            "question": new_query,
            "answer": reply
        }
        
    if st.session_state.current_qa is None:
        st.chat_message("assistant", avatar=ASSISTANT_AVATAR).write(WELCOME_MESSAGE)
    else:
        st.chat_message("user", avatar=USER_AVATAR).write(st.session_state.current_qa["question"])
        st.chat_message("assistant", avatar=ASSISTANT_AVATAR).write(st.session_state.current_qa["answer"])