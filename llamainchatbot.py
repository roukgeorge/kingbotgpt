import os
import glob
import json
import time
import threading
import unicodedata
from datetime import datetime
import requests
import streamlit as st
from google import genai
from google.genai import types
import toml

cbconfig = toml.load("cbconfig.toml")

USER_AVATAR = "👤"
ASSISTANT_AVATAR = "🤖"
DATA_FILE = "ihu_library_data.txt"

HIDEMENU = """
<style>
.stApp [data-testid="stHeader"] {display:none;}
p img {margin-bottom: 0.6rem;}
[data-testid="stSidebarCollapseButton"] {display:none;}
[data-testid="baseButton-headerNoPadding"] {display:none;}
</style>
"""

WELCOME_MESSAGE = (
    "Καλώς ήρθατε στον ψηφιακό βοηθό των **Βιβλιοθηκών του ΔΙΠΑΕ**! 👋\n\n"
    "Μπορείτε να επιλέξετε ένα από τα παραπάνω θέματα ή να πληκτρολογήσετε την ερώτησή σας "
    "σχετικά με τα ωράρια λειτουργίας (Σίνδος, Σέρρες, Θέρμη), τον δανεισμό βιβλίων και τις υπηρεσίες."
)

STOPWORDS = {
    "και", "για", "απο", "στη", "στην", "στο", "στον", "στης", "στους", "στις",
    "των", "του", "της", "τον", "την", "ενα", "μια", "ενας", "ειναι", "εχει",
    "εχουν", "πως", "ποιο", "ποια", "ποιος", "ποιες", "ποια", "τι", "ωρα",
    "μπορω", "θελω", "σχετικα", "με", "σε", "ως", "οτι", "αν", "θα", "να", "τα", "οι", "το"
}

def normalize_greek(text):
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(ch) != "Mn"
    )

def extract_stems(text):
    norm = normalize_greek(text)
    cleaned = "".join(ch if ch.isalnum() else " " for ch in norm)
    stems = set()
    for word in cleaned.split():
        if len(word) >= 3 and word not in STOPWORDS:
            stems.add(word[:5] if len(word) >= 5 else word)
    return stems

@st.cache_resource(show_spinner=False)
def load_and_chunk_knowledge():
    if not os.path.exists(DATA_FILE):
        return "", []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        full_text = f.read().strip()

    chunks = []
    current_source = "https://www.ihu.gr/vivliothiki-kentro-pliroforisis"
    chunk_size = 1200

    for line in full_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("=== ΠΗΓΗ:"):
            current_source = line.replace("=== ΠΗΓΗ:", "").replace("===", "").strip()
            continue
        # Αν μια γραμμή είναι πολύ μεγάλη, τη σπάμε σε μικρότερα κομμάτια
        for i in range(0, len(line), chunk_size):
            piece = line[i:i + chunk_size]
            chunks.append((f"[{current_source}]\n{piece}", extract_stems(piece)))

    return full_text, chunks

def get_relevant_context(query, max_chunks=10):
    full_text, chunks = load_and_chunk_knowledge()
    if len(full_text) <= 15000 or not chunks:
        return full_text

    query_stems = extract_stems(query)
    if not query_stems:
        return "\n\n".join(c[0] for c in chunks[:max_chunks])

    scored = []
    for idx, (chunk_text, chunk_stems) in enumerate(chunks):
        score = sum(1 for s in query_stems if any(cs.startswith(s) or s.startswith(cs) for cs in chunk_stems))
        scored.append((score, idx, chunk_text))

    scored.sort(key=lambda x: (-x[0], x[1]))
    top_chunks = sorted(scored[:max_chunks], key=lambda x: x[1])
    return "\n\n---\n\n".join(c[2] for c in top_chunks)

@st.cache_resource(ttl="1d", show_spinner=False)
def get_available_models(_client):
    preferred = ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"]
    try:
        available = []
        for m in _client.models.list():
            name = m.name.replace("models/", "")
            if "flash" in name and "image" not in name and "audio" not in name and "tts" not in name:
                available.append(name)
        ordered = [m for m in preferred if m in available]
        for m in available:
            if m not in ordered:
                ordered.append(m)
        if ordered:
            return ordered
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

def _send_stat_worker(webhook_url, payload):
    try:
        requests.post(webhook_url, json=payload, timeout=10)
    except Exception:
        pass

def log_anonymous_stat_async(source, category, question, answered):
    webhook_url = st.secrets.get("STATS_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return
    payload = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "category": category,
        "question": question,
        "answered": "Ναι" if answered else "Όχι"
    }
    threading.Thread(target=_send_stat_worker, args=(webhook_url, payload), daemon=True).start()

def build_system_prompt(relevant_content):
    return f"""
Είσαι ο επίσημος ψηφιακός βοηθός των Βιβλιοθηκών και Κέντρων Πληροφόρησης του Διεθνούς Πανεπιστημίου της Ελλάδος (ΔΙΠΑΕ).
Απαντάς πάντα στα ελληνικά με ευγένεια, σαφήνεια και απόλυτη ακρίβεια.

ΑΥΣΤΗΡΟΙ ΚΑΝΟΝΕΣ:
1. Οι Βιβλιοθήκες του ΔΙΠΑΕ λειτουργούν αποκλειστικά σε Σίνδο, Σέρρες και Θέρμη. Δεν λειτουργούν σε άλλες πόλεις.
2. Βασίσου ΑΠΟΚΛΕΙΣΤΙΚΑ στα παρακάτω επίσημα αποσπάσματα των Βιβλιοθηκών του ΔΙΠΑΕ. Μην επινοείς κανόνες, ώρες ή αριθμούς.
3. Όταν απαντάς, ανέφερε στο τέλος τη σχετική επίσημη σελίδα (URL) εφόσον αναφέρεται στα αποσπάσματα, αλλιώς το https://www.ihu.gr/vivliothiki-kentro-pliroforisis.
4. Αν μια πληροφορία δεν υπάρχει καθόλου στα παρακάτω αποσπάσματα, δήλωσε ξεκάθαρα ότι δεν αναφέρεται αναλυτικά στις διαθέσιμες πληροφορίες και πρότεινε στον χρήστη να επισκεφθεί την επίσημη ιστοσελίδα (https://www.ihu.gr/vivliothiki-kentro-pliroforisis) ή να επικοινωνήσει απευθείας με το προσωπικό της βιβλιοθήκης (αναφέροντας και τα σχετικά τηλέφωνα ή email, αν υπάρχουν).

ΜΟΡΦΗ ΕΞΟΔΟΥ (ΑΥΣΤΗΡΑ JSON):
Επίστρεψε ΑΠΟΚΛΕΙΣΤΙΚΑ ένα έγκυρο αντικείμενο JSON με τα εξής 4 πεδία:
- "answer": Η αναλυτική απάντησή σου στα ελληνικά (με Markdown μορφοποίηση).
- "category": Μία από τις εξής κατηγορίες: "Ωράριο Λειτουργίας", "Δανεισμός & Κανόνες", "Παράρτημα Σίνδου", "Παράρτημα Σερρών", "Παράρτημα Θέρμης", "Ηλεκτρονικές Πηγές & Βάσεις", "Επικοινωνία & Προσωπικό", "Άλλο".
- "sanitized_question": Η ερώτηση του χρήστη όπως ακριβώς γράφτηκε, ΑΛΛΑ αν περιέχει ονοματεπώνυμο, Αριθμό Μητρώου, email ή τηλέφωνο, αντικατάστησέ τα με "[ΑΝΩΝΥΜΟ]" για συμμόρφωση με τον GDPR.
- "answered": true αν η απάντηση βρέθηκε στα παρακάτω αποσπάσματα, ή false αν η πληροφορία δεν υπήρχε και παρέπεμψες στο προσωπικό.

ΣΧΕΤΙΚΑ ΑΠΟΣΠΑΣΜΑΤΑ ΒΙΒΛΙΟΘΗΚΩΝ ΔΙΠΑΕ:
{relevant_content}
"""

@st.cache_data(ttl="1d", show_spinner=False)
def cached_ai_query(normalized_key, user_query, _client):
    models_to_try = get_available_models(_client)
    relevant_content = get_relevant_context(user_query)
    full_instructions = build_system_prompt(relevant_content)

    for model_name in models_to_try[:3]:
        for attempt in range(2):
            try:
                response = _client.models.generate_content(
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
                    return (
                        True,
                        data.get("answer", ""),
                        data.get("category", "Άλλο"),
                        data.get("sanitized_question", user_query),
                        bool(data.get("answered", True))
                    )
            except Exception as e:
                err_str = str(e)
                if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str:
                    time.sleep(1.0)
                    continue
                break

    fallback = (
        "Η υπηρεσία τεχνητής νοημοσύνης δέχεται αυξημένο φόρτο αυτή τη στιγμή. "
        "Παρακαλούμε πατήστε ξανά το κουμπί ή ξαναστείλτε την ερώτησή σας σε λίγα δευτερόλεπτα, "
        "ή επισκεφθείτε την επίσημη σελίδα: https://www.ihu.gr/vivliothiki-kentro-pliroforisis"
    )
    return False, fallback, "Άλλο", user_query, False

def generate_reply(user_query, source_label, client):
    norm_key = normalize_greek(user_query).strip()
    ok, answer, category, sanitized_q, answered = cached_ai_query(norm_key, user_query, client)
    if not ok:
        cached_ai_query.clear()
    log_anonymous_stat_async(source_label, category, sanitized_q, answered)
    return answer

if __name__ == "__main__":
    st.set_page_config(page_title="Βιβλιοθήκες ΔΙΠΑΕ - AI Chatbot", page_icon="🤖", initial_sidebar_state="expanded")
    st.markdown(HIDEMENU, unsafe_allow_html=True)

    client = genai.Client(
        api_key=st.secrets["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=20000)
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
        full_kb, _ = load_and_chunk_knowledge()
        kb_size = f"{len(full_kb):,}".replace(",", ".")
        st.sidebar.info(
            "**Αδειοδότηση & Συμμόρφωση:**\n\n"
            "Προσαρμογή του ανοικτού λογισμικού *Kingbot (SJSU Library - MIT License)* "
            "για τις Βιβλιοθήκες του ΔΙ.ΠΑ.Ε., σύμφωνα με την ελληνική νομοθεσία "
            "(Ν. 2121/1993 & Ν. 4727/2020) και τον Κανονισμό Λειτουργίας του Ιδρύματος.\n\n"
            "**Προστασία Δεδομένων (GDPR):**\n"
            "Δεν συλλέγονται προσωπικά δεδομένα (Καν. ΕΕ 2016/679 & Ν. 4624/2019). "
            "Καταγράφονται αποκλειστικά ανωνυμοποιημένα ερωτήματα και στατιστικά χρήσης για τη βελτίωση των υπηρεσιών. "
            "Παρακαλούμε μην καταχωρείτε προσωπικά στοιχεία (π.χ. ονοματεπώνυμο, ΑΜ).\n\n"
            "**Σημείωση AI:**\n"
            "Οι απαντήσεις παράγονται αυτόματα από επίσημα κείμενα του ΔΙ.ΠΑ.Ε. Για δεσμευτικά ζητήματα ισχύουν οι επίσημες ανακοινώσεις.\n\n"
            f"**Βάση Γνώσης ΔΙ.ΠΑ.Ε.:** {kb_size} χαρακτήρες επίσημων κειμένων."
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
        st.session_state.current_qa = {"question": new_query, "answer": reply}

    if st.session_state.current_qa is None:
        st.chat_message("assistant", avatar=ASSISTANT_AVATAR).write(WELCOME_MESSAGE)
    else:
        st.chat_message("user", avatar=USER_AVATAR).write(st.session_state.current_qa["question"])
        st.chat_message("assistant", avatar=ASSISTANT_AVATAR).write(st.session_state.current_qa["answer"])