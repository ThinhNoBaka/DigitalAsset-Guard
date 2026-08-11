"""
db/vector_db.py
Khởi tạo ChromaDB local, load và embed nội dung từ data/legal_docs/
(Thông tư 27/2025 và Thông tư 32/2026).
"""
import os
import chromadb
from chromadb.utils import embedding_functions

LEGAL_DOCS_DIR = "data/legal_docs"
CHROMA_DB_DIR = "db/chroma_db"
# [2026-08-09] Chủ đích bỏ Thông tư 32/2026/TT-BTC (tính năng thuế tài sản mã hóa).
# Bổ sung fatf_recommendations.txt để ingest nguồn chuẩn mực quốc tế FATF.
# [2026-08-09] Bổ sung luat_pcrt_2022_qd11_2023.txt — văn bản GỐC (Luật PCRT 2022
# + Quyết định 11/2023/QĐ-TTg) chứa NGƯỠNG SỐ 400.000.000 đồng cho giao dịch giá
# trị lớn (Điều 25 Luật PCRT). Thông tư 27/2025/TT-NHNN chỉ dẫn chiếu, không tự
# nêu con số — LLM không thể khẳng định "400 triệu" nếu nguồn này không được ingest.
REQUIRED_FILES = [
    "thong_tu_27_2025.txt",
    "fatf_recommendations.txt",
    "luat_pcrt_2022_qd11_2023.txt",
]


def init_and_load_vector_db():
    """Khởi tạo ChromaDB local, load và embed nội dung từ Thông tư 27 và Thông tư 32."""
    print(f"[*] Đang khởi tạo ChromaDB tại {CHROMA_DB_DIR}...")
    client = chromadb.PersistentClient(path=CHROMA_DB_DIR)

    sentence_transformer_ef = embedding_functions.DefaultEmbeddingFunction()
    collection = client.get_or_create_collection(
        name="legal_regulations",
        embedding_function=sentence_transformer_ef
    )

    if collection.count() > 0:
        print(f"[*] Database đã có sẵn {collection.count()} chunks dữ liệu. Bỏ qua bước load.")
        return collection

    documents, metadatas, ids = [], [], []
    doc_id_counter = 1
    missing_files = []

    for filename in REQUIRED_FILES:
        filepath = os.path.join(LEGAL_DOCS_DIR, filename)
        if not os.path.exists(filepath):
            missing_files.append(filename)
            print(f"[!] CẢNH BÁO: Không tìm thấy file {filepath}.")
            continue

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            chunks = [c.strip() for c in content.split('\n\n') if len(c.strip()) > 15]
            for chunk in chunks:
                documents.append(chunk)
                metadatas.append({"source": filename})
                ids.append(f"doc_{doc_id_counter}")
                doc_id_counter += 1

    if documents:
        collection.add(documents=documents, metadatas=metadatas, ids=ids)
        print(f"[+] Đã embed thành công {len(documents)} chunks từ {len(REQUIRED_FILES) - len(missing_files)} nguồn luật.")

    # Kiểm tra bắt buộc: cả 2 nguồn luật phải có mặt trong collection sau khi load
    if missing_files:
        print(f"[!] LƯU Ý: RAG sẽ không thể trích dẫn từ: {missing_files}. "
              f"Test 'thuế tài sản số' (Thông tư 32) sẽ FAIL nếu file này thiếu.")

    sources_in_db = {m["source"] for m in metadatas}
    for req in REQUIRED_FILES:
        if req not in sources_in_db and req not in missing_files:
            print(f"[!] CẢNH BÁO: {req} tồn tại nhưng không tạo ra chunk nào hợp lệ (nội dung quá ngắn?).")

    return collection


def query_legal_docs(query_text: str, n_results: int = 3) -> dict:
    """Hàm hỗ trợ truy vấn ChromaDB cho Agent."""
    client = chromadb.PersistentClient(path=CHROMA_DB_DIR)
    collection = client.get_collection(name="legal_regulations")
    return collection.query(query_texts=[query_text], n_results=n_results)


if __name__ == "__main__":
    init_and_load_vector_db()