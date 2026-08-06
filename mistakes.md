Ghi vào báo cáo (phần "Hạn chế") — những gì code không tự giải quyết được

Về wallet clustering: không cần viết thành mục hạn chế riêng — chỉ cần 1 dòng ghi chú trong báo cáo (và trong code) rằng: "Wallet clustering không triển khai ở KYC Assistant mà gộp vào Graph Assistant (Louvain Community Detection, Phần 6), vì bản chất clustering ví dựa trên đồ thị giao dịch, tránh trùng lặp logic." Đây coi như đã giải quyết, không phải hạn chế thật.

Về độ phủ OFAC theo ví — đây là hạn chế THẬT, cần viết rõ vào báo cáo:

Hệ thống hiện tại chỉ gắn cờ cảnh báo qua địa chỉ ví nếu OFAC SDN có đính kèm sẵn địa chỉ ví crypto cho entity đó, và/hoặc nếu ví liên quan có liên kết đồ thị (1-2 hop) tới một ví đã biết trong SDN (qua Graph Assistant/PPR). Tuy nhiên, phần lớn entry trong sdn.xml chỉ có tên/thông tin định danh, không có địa chỉ ví — với các entity này, nếu không có bất kỳ liên kết giao dịch nào tới ví đã biết, Graph Assistant cũng không có "điểm neo" (seed) để lan truyền rủi ro, nên hệ thống sẽ không phát hiện được qua kênh ví. Việc so khớp tên (fuzzy match) đã được xử lý riêng ở tầng webhook trước khi băm PII nên phần tên vẫn được kiểm tra đầy đủ — hạn chế chỉ nằm ở kênh địa chỉ ví. Đây là đánh đổi có chủ đích để bảo vệ PII (không so khớp tên trên dữ liệu đã băm ở tầng agent), cần nêu rõ khi bảo vệ.

(Nếu muốn có con số % cụ thể entry nào có ví trong sdn.xml, mình có thể viết script đếm khi bạn upload file.)