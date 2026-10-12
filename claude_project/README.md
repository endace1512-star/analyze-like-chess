# Claude Project — Like a Chess Game (LACG)

Bộ file để dựng Claude Project cho kênh **Like a Chess Game**: phân tích các cảnh đấu trí trong series/phim như một ván cờ, không VO, toàn bộ bình luận nằm trong thẻ chữ gõ ra trên khung hình dừng.

## Cài đặt Project (làm 1 lần)

1. Trên claude.ai tạo Project mới tên **Like a Chess Game**.
2. **Project instructions**: dán toàn bộ nội dung `LACG_command_instructions.md`.
3. **Project knowledge**: upload 3 file:
   - `LACG-CHANNEL-DNA.md` — thông tin kênh, công thức title, cấu trúc video, tiêu chí chọn cảnh, thumbnail, footer mô tả, khác biệt với kênh mẫu.
   - `LACG-STYLE-GUIDE.md` — giọng "The Engine", nhịp nước đi, cách viết thẻ, bảng xếp loại nước, luật eval, bảng quy đổi truyện → cờ, tên khai cuộc, danh sách cấm.
   - `LACG-CONTENT-BANK.md` — cảnh đã làm, nhãn nhân vật dùng lại, kho TAG, kho cảnh, mẫu cụm từ tìm kiếm.
4. Sau mỗi tập: thêm 1 dòng vào §1 của CONTENT-BANK (và nhân vật mới vào §2), upload lại file đó.

## Chat nào chạy lệnh nào

| Chat | Lệnh | Model | Web search |
|---|---|---|---|
| 1 chat riêng, dùng lâu dài | `/pick` | Sonnet | BẬT |
| 1 chat MỚI cho mỗi tập | `/analyze` → `/qcmoves` → `/metadata` → `/thumbnail` | Opus cho `/analyze`, Sonnet cho phần còn lại | tắt |

## Quy trình 1 tập

| Bước | Ở đâu | Bạn đưa vào | Nhận về | Mang đi đâu |
|---|---|---|---|---|
| 1 | Claude `/pick` | (tuỳ chọn) tên series + done list từ app | 6 cảnh đề xuất | Chọn 1 cảnh, tự cắt clip mp4 |
| 2 | App · tab THOẠI | clip mp4 | khối `=== TRANSCRIPT ===` (có tên người nói) + `dialogue.srt` | Copy TRANSCRIPT sang chat mới |
| 3 | Claude `/analyze` | TRANSCRIPT + `context:` + `notes:` (mốc hình ảnh) | khối `=== MOVES ===` | Chạy `/qcmoves` ngay sau |
| 4 | Claude `/qcmoves` | (tự đọc trong chat) | báo cáo + PASS/FAIL + **khối MOVES cuối cùng** | Dán khối MOVES vào app |
| 5 | App · tab NƯỚC ĐI | khối MOVES | bảng nước đi đã khớp thời gian (dòng đỏ = cần sửa) | — |
| 6 | App · SINH DRAFT | — | draft CapCut + khối `=== CHAPTERS ===` + ảnh khung hình `M<k>` | Mở CapCut; copy CHAPTERS sang chat |
| 7 | CapCut | — | video mp4 | YouTube |
| 8 | Claude `/metadata` | `chapters:` + khối CHAPTERS | 9 title, title chính, bộ A/B, description, tags, 4 comment | YouTube Studio |
| 9 | Claude `/thumbnail` | (tự đọc trong chat) | 9 spec thumbnail (tsv) | Dán vào app · tab THUMBNAIL |
| 10 | App · tab THUMBNAIL | spec | 9 ảnh PNG 1280×720 | YouTube Test & Compare |

## Các khối dữ liệu là "hợp đồng" giữa Claude và app

Ba khối `=== TRANSCRIPT ===`, `=== MOVES ===`, `=== CHAPTERS ===` và dòng tsv của `/thumbnail` được định nghĩa chặt trong `LACG_command_instructions.md`. App đọc đúng các định dạng đó — nếu sửa định dạng ở đây thì phải sửa cả parser trong app.
