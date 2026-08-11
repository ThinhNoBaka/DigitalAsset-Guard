"""Debug retrieval — xem chunk nào được ChromaDB trả về cho 2 query RAG dùng."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.vector_db import query_legal_docs

STR_QUERY = (
    "Nghĩa vụ báo cáo giao dịch đáng ngờ (STR): dấu hiệu đáng ngờ liên quan "
    "đến rửa tiền, tài trợ khủng bố; báo cáo không phụ thuộc vào lượng tiền "
    "giao dịch."
)
LARGE_TX_QUERY = (
    "Mức giao dịch có giá trị lớn phải báo cáo (CTR): ngưỡng 400.000.000 "
    "đồng trở lên theo Quyết định 11/2023/QĐ-TTg, thẩm quyền Thủ tướng "
    "Chính phủ, Điều 25 Luật PCRT 2022."
)
LARGE_TX_N = 8

print("=" * 80)
print(f"QUERY 1 - STR (n_results=5)")
print("=" * 80)
r1 = query_legal_docs(query_text=STR_QUERY, n_results=5)
for i, (doc, meta) in enumerate(zip(r1["documents"][0], r1["metadatas"][0])):
    print(f"\n--- [{i + 1}] source={meta['source']} ---")
    print(doc[:400])
    print("...(TRUNCATED)" if len(doc) > 400 else "")

print("\n" + "=" * 80)
print(f"QUERY 2 - LARGE TX CTR (n_results={LARGE_TX_N})")
print("=" * 80)
r2 = query_legal_docs(query_text=LARGE_TX_QUERY, n_results=LARGE_TX_N)
for i, (doc, meta) in enumerate(zip(r2["documents"][0], r2["metadatas"][0])):
    print(f"\n--- [{i + 1}] source={meta['source']} ---")
    print(doc[:400])
    print("...(TRUNCATED)" if len(doc) > 400 else "")

print("\n" + "=" * 80)
print("CHECK: chunk '400.000.000' co trong top-8 khong?")
print("=" * 80)
for label, r in (("STR", r1), ("LARGE_TX", r2)):
    found = False
    for i, doc in enumerate(r["documents"][0]):
        if "400.000.000" in doc:
            print(f"  OK {label} top-{i + 1}: co chua '400.000.000'")
            found = True
            break
    if not found:
        print(f"  FAIL {label}: KHONG co chunk nao chua '400.000.000' trong top-{LARGE_TX_N if label == 'LARGE_TX' else 5}")