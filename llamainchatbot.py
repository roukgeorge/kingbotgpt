import os
import glob
import json
import time
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

def load_library_knowledge():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

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
    library_content = load_library_knowledge()
    return f"""
Είσαι ο επίσημος ψηφιακός βοηθός των Βιβλιοθηκών και Κέντρων Πληροφόρησης του Διεθνούς Πανεπιστημίου της Ελλάδος (ΔΙΠΑΕ).
Απαντάς πάντα στα ελληνικά με ευγένεια, σαφήνεια και απόλυτη ακρίβεια.

ΑΥΣΤΗΡΟΙ ΚΑΝΟΝΕΣ:
1. Οι Βιβλιοθήκες του ΔΙΠΑΕ λειτουργούν αποκλειστικά σε Σίνδο, Σέρρες και Θέρμη. Δεν λειτουργούν σε άλλες πόλεις.
2. Βασίσου ΑΠΟΚΛΕΙΣΤΙΚΑ στα παρακάτω επίσημα κείμενα των Βιβλιοθηκών του ΔΙΠΑΕ (Ωράρια, Συχνές Ερωτήσεις, Κανονισμοί, Υπηρεσίες). Μην επινοείς κανόνες, ώρες ή αριθμούς.
3. Όταν απαντάς, ανέφερε στο τέλος τη σχετική επίσημη σελίδα (URL) εφόσον αναφέρεται στα κείμενα, αλλιώς το https://www.ihu.gr/vivliothiki-kentro-pliroforisis.
4. Αν μια πληροφορία δεν υπάρχει καθόλου στα παρακάτω κείμενα, δήλωσε ξεκάθαρα ότι δεν αναφέρεται αναλυτικά στις διαθέσιμες πληροφορίες και πρότεινε στον χρήστη να επισκεφθεί την επίσημη ιστοσελίδα (https://www.ihu.gr/vivliothiki-kentro-pliroforisis) ή να επικοινωνήσει απευθείας με το προσωπικό της βιβλιοθήκης (αναφέροντας και τα σχετικά τηλέφωνα ή email, αν υπάρχουν στα παρακάτω κείμενα).

ΜΟΡΦΗ ΕΞΟΔΟΥ (ΑΥΣΤΗΡΑ JSON):
Επίστρεψε ΑΠΟΚΛΕΙΣΤΙΚΑ ένα έγκυρο αντικείμενο JSON με τα εξής 4 πεδία:
- "answer": Η αναλυτική απάντησή σου στα ελληνικά (με Markdown μορφοποίηση).
- "category": Μία από τις εξής κατηγορίες: "Ωράριο Λειτουργίας", "Δανεισμός & Κανόνες", "Παράρτημα Σίνδου", "Παράρτημα Σερρών", "Παράρτημα Θέρμης", "Ηλεκτρονικές Πηγές & Βάσεις", "Επικοινωνία & Προσωπικό", "Άλλο".
- "sanitized_question": Η ερώτηση του χρήστη όπως ακριβώς γράφτηκε, ΑΛΛΑ αν περιέχει ονοματεπώνυμο, Αριθμό Μητρώου, email ή τηλέφωνο, αντικατάστησέ τα με "[ΑΝΩΝΥΜΟ]" για συμμόρφωση με τον GDPR.
- "answered": true αν η απάντηση βρέθηκε στα παρακάτω κείμενα, ή false αν η πληροφορία δεν υπήρχε και παρέπεμψες στο προσωπικό.

ΕΠΙΣΗΜΟ ΠΕΡΙΕΧΟΜΕΝΟ ΒΙΒΛΙΟΘΗΚΩΝ ΔΙΠΑΕ:
{library_content}
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
                log_anonymous_stat(
                    source_label,
                    data.get("category", "Άλλο"),
                    data.get("sanitized_question", user_query),
                    bool(data.get("answered", True))
                )
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
        kb_size = f"{len(load_library_knowledge()):,}".replace(",", ".")
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