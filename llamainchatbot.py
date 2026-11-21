import os
import glob
import time
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
import streamlit as st
from google import genai
from google.genai import types
import toml

cbconfig = toml.load("cbconfig.toml")
AVATARS = cbconfig['AVATARS']

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
    "https://www.ihu.gr/vivliothiki-kentro-pliroforisis/sindos",
    "https://www.ihu.gr/vivliothiki-kentro-pliroforisis/serres",
    "https://www.ihu.gr/vivliothiki-kentro-pliroforisis"
]

@st.cache_resource(ttl="1d", show_spinner="Φόρτωση και ευρετηρίαση όλων των υπομενού της Βιβλιοθήκης ΔΙΠΑΕ...")
def fetch_all_library_pages():
    headers = {"User-Agent": "Mozilla/5.0"}
    visited = set()
    urls_to_crawl = list(BASE_URLS)
    
    for base_url in BASE_URLS:
        try:
            res = requests.get(base_url, headers=headers, timeout=5)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                for a_tag in soup.find_all("a", href=True):
                    full_url = urljoin(base_url, a_tag["href"]).split("#")[0].rstrip("/")
                    if "ihu.gr/vivliothiki-kentro-pliroforisis" in full_url and full_url not in urls_to_crawl:
                        urls_to_crawl.append(full_url)
        except Exception:
            continue

    extracted_text = ""
    for url in urls_to_crawl[:18]:
        if url in visited:
            continue
        visited.add(url)
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                for tag in soup(["script", "style", "footer", "header"]):
                    tag.extract()
                main_content = soup.find("main") or soup.find("article") or soup
                text = " ".join(main_content.stripped_strings)
                extracted_text += f"\n\n=== ΠΗΓΗ: {url} ===\n{text}"
        except Exception:
            continue
            
    return extracted_text

@st.cache_resource(ttl="1d", show_spinner=False)
def get_available_models(_client):
    # Ξεκινάμε με τα γνωστά ενεργά μοντέλα και προσθέτουμε αυτόματα όποιο άλλο flash είναι διαθέσιμο στον λογαριασμό
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

def get_system_prompt():
    live_content = fetch_all_library_pages()
    return f"""
Είσαι ο επίσημος ψηφιακός βοηθός της Βιβλιοθήκης και Κέντρου Πληροφόρησης του Διεθνούς Πανεπιστημίου της Ελλάδος (ΔΙΠΑΕ).
Απαντάς πάντα στα ελληνικά με ευγένεια, σαφήνεια και απόλυτη ακρίβεια.

ΑΥΣΤΗΡΟΙ ΚΑΝΟΝΕΣ:
1. Η Βιβλιοθήκη του ΔΙΠΑΕ λειτουργεί αποκλειστικά σε Σίνδο, Σέρρες και Θέρμη. Δεν λειτουργεί σε άλλες πόλεις.
2. Βασίσου ΑΠΟΚΛΕΙΣΤΙΚΑ στα παρακάτω επίσημα κείμενα που αντλήθηκαν από τις ιστοσελίδες και τα υπομενού (Συχνές Ερωτήσεις, Κανονισμοί, Υπηρεσίες) της Βιβλιοθήκης του ΔΙΠΑΕ. Μην επινοείς κανόνες ή αριθμούς.
3. Όταν απαντάς, ανέφερε στο τέλος τη σχετική επίσημη σελίδα (URL) από τις πηγές παρακάτω ώστε ο χρήστης να μπορεί να τη δει αναλυτικά.
4. Αν μια πληροφορία δεν υπάρχει καθόλου στα παρακάτω κείμενα, δήλωσε ξεκάθαρα ότι δεν αναφέρεται στις σελίδες της βιβλιοθήκης και παράπεμψε στο https://www.ihu.gr/vivliothiki-kentro-pliroforisis.

ΕΠΙΣΗΜΟ ΠΕΡΙΕΧΟΜΕΝΟ ΙΣΤΟΣΕΛΙΔΩΝ & ΥΠΟΜΕΝΟΥ ΔΙΠΑΕ:
{live_content}
"""

def process_query(user_query, client):
    models_to_try = get_available_models(client)
    full_instructions = get_system_prompt()
    
    with st.spinner(text="Αναζήτηση στα δεδομένα της βιβλιοθήκης..."):
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
                    )
                )
                if response and response.text:
                    answer = response.text
                    break
            except Exception as e:
                last_error = str(e)
                if "503" in last_error or "UNAVAILABLE" in last_error:
                    time.sleep(2)
                continue
        
        if not answer:
            answer = f"Προέκυψε προσωρινό σφάλμα επικοινωνίας με το AI ({last_error}). Παρακαλώ ξαναστείλτε την ερώτηση."
            
        st.session_state.current_qa = {
            "question": user_query,
            "answer": answer
        }

if __name__ == "__main__":
    st.set_page_config(page_title="Βιβλιοθήκη ΔΙΠΑΕ - AI Chatbot", page_icon="🤖", initial_sidebar_state="expanded")
    st.markdown(HIDEMENU, unsafe_allow_html=True)
    
    client = genai.Client(
        api_key=st.secrets["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=15000)
    )
    
    # Αριστερή πλαϊνή μπάρα με το λογότυπο του ΔΙΠΑΕ
    logo_path = find_logo()
    if logo_path:
        st.sidebar.image(logo_path, use_container_width=True)
        st.sidebar.markdown("\n")
        
    st.sidebar.markdown(cbconfig['side']['title'])
    st.sidebar.markdown(cbconfig['side']['intro'])
    st.sidebar.markdown("\n\n")
    st.sidebar.link_button("Βιβλιοθήκη ΔΙΠΑΕ", "https://www.ihu.gr/vivliothiki-kentro-pliroforisis")
    
    # Κεντρικός τίτλος
    col1, col2, col3 = st.columns([0.25, 0.1, 0.65], vertical_alignment="bottom")
    with col2:
        st.markdown(cbconfig['main']['logo'])
    with col3:
        st.title("Βιβλιοθήκη ΔΙΠΑΕ")
    st.markdown("\n\n")
    
    # Κουμπιά γρήγορων ερωτήσεων
    col21, col22, col23 = st.columns(3)
    with col21:
        button1 = st.button("📍 Παράρτημα Σίνδου")
    with col22:
        button2 = st.button("📍 Παράρτημα Σερρών")    
    with col23:
        button3 = st.button("❓ Συχνές Ερωτήσεις & Δανεισμός")    
    
    if 'current_qa' not in st.session_state:
        st.session_state.current_qa = None
        
    user_query = st.chat_input(placeholder="Ρωτήστε για ωράρια, δανεισμό, συχνές ερωτήσεις, Σίνδο ή Σέρρες...")
    
    new_query = None
    if button1:
        new_query = "Ποιο είναι το ωράριο, τα τηλέφωνα επικοινωνίας και οι υπηρεσίες της βιβλιοθήκης στη Σίνδο;"
    elif button2:
        new_query = "Ποιο είναι το ωράριο, τα τηλέφωνα επικοινωνίας και οι υπηρεσίες της βιβλιοθήκης στις Σέρρες;"
    elif button3:
        new_query = "Τι αναφέρουν οι συχνές ερωτήσεις και οι κανονισμοί σχετικά με τον δανεισμό στις βιβλιοθήκες του ΔΙΠΑΕ;"
    elif user_query:
        new_query = user_query
        
    if new_query:
        process_query(new_query, client)
        
    if st.session_state.current_qa:
        st.chat_message("user", avatar=AVATARS["user"]).write(st.session_state.current_qa["question"])
        st.chat_message("assistant", avatar=AVATARS["assistant"]).write(st.session_state.current_qa["answer"])