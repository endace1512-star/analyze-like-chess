# Chess Studio — cài đặt & dùng (kênh Like a Chess Game)

App dựng video cho kênh: nhận thoại có tên người nói → nhận khối MOVES từ Claude Project → sinh **draft CapCut** đã edit sẵn (dừng hình, badge, thẻ gõ chữ, thanh eval, phụ đề, SFX, Game Review) → thumbnail.

## Các file

| File | Vai trò |
|---|---|
| `chess_studio.pyw` | App (giao diện). Mở bằng `chess_studio.bat` |
| `lacg_formats.py` | Đọc/ghi các khối TRANSCRIPT · MOVES · CHAPTERS · spec thumbnail, khớp anchor |
| `lacg_transcribe.py` | Nhận thoại: Demucs → WhisperX → pyannote → vân tay giọng (chạy nền, GPU) |
| `lacg_render.py` | Vẽ badge, thẻ, thanh eval, ô player, Game Review, thumbnail; render đoạn dừng hình |
| `lacg_build.py` | Sinh draft CapCut + chapters + khung hình |
| `lacg_check.py` | Kiểm tra cài đặt (nút KIỂM TRA CÀI ĐẶT ở tab KÊNH) |
| `install_lacg.bat` | Cài môi trường Python (một lần) |
| `requirements_lacg.txt` | Danh sách thư viện |
| `sfx/` | Âm thanh — xem `sfx/README.txt` |
| `claude_project/` | Bộ file cho Claude Project + quy trình 1 tập |

Đặt **tất cả trong cùng một thư mục**.

## Cài đặt (một lần, Windows + card NVIDIA)

1. **Driver NVIDIA** bản mới, rồi **CUDA Toolkit 12.8** (WhisperX yêu cầu): developer.nvidia.com → CUDA Toolkit Archive → 12.8.1.
2. **FFmpeg bản _shared_** (có thư mục `bin` chứa các file `.dll`), bản **7.x**: tải từ gyan.dev (gói `full_build-shared`), giải nén, thêm thư mục `bin` vào PATH. Bản shared vừa cho lệnh `ffmpeg`/`ffplay`, vừa cho thư viện `torchcodec` mà WhisperX cần.
3. **Python 3.12** từ python.org — tick **"Add python.exe to PATH"** (bản này có sẵn giao diện tkinter và lệnh `py`).
4. Chạy **`install_lacg.bat`**. Script tạo `venv\`, cài torch 2.8 bản CUDA 12.8 (~3 GB), rồi whisperx, demucs, pycapcut, pillow, opencv, cuối cùng tự chạy kiểm tra.
5. **HuggingFace** (để phân biệt người nói):
   - Tạo token loại **Read** ở huggingface.co/settings/tokens.
   - Mở từng trang dưới, đăng nhập, bấm **Agree/Access**:
     - `huggingface.co/pyannote/speaker-diarization-community-1`
     - `huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM`
     - (tuỳ chọn, cho "tách giọng nói chồng") `huggingface.co/pyannote/speech-separation-ami-1.0`
6. Mở app bằng **`chess_studio.bat`** → tab **KÊNH**: dán token, kiểm tra thư mục draft CapCut, chọn thư mục SFX → **LƯU CÀI ĐẶT** → **KIỂM TRA CÀI ĐẶT**. Mọi dòng phải ✅ (⚠️ là tuỳ chọn).

Lần nhận thoại đầu tiên app tải model Whisper large-v3 (~3 GB), Demucs và pyannote — chậm một lần, sau đó dùng lại.

## Dùng — một tập

Quy trình đầy đủ (kèm phần Claude Project) ở `claude_project/README.md`. Phần trong app:

1. **＋ Tạo tập mới từ clip…** → chọn file mp4 của cảnh. Mỗi tập là một thư mục trong "Thư mục chứa các tập".
2. Tab **THOẠI**:
   - (Tuỳ chọn) chọn **phụ đề chuẩn .srt** của tập phim → app lấy CHỮ từ phụ đề (chính xác hơn khi cãi nhau), máy chỉ xác định AI nói. App tự tìm độ lệch giờ giữa phụ đề cả tập và clip.
   - **NHẬN THOẠI** → chờ log báo ✅.
   - Khung **NGƯỜI NÓI**: bấm ▶ nghe mẫu, gõ tên IN HOA (GUS, HANK…) → **Áp dụng tên + lưu giọng**. Lần sau gặp lại giọng đó app tự điền.
   - Bảng thoại: dòng **cam** = nói chồng, **đỏ** = chưa rõ ai nói → nghe (▶ Nghe dòng), sửa người nói / chữ bằng double-click, Tách / Gộp / Thêm dòng khi cần. Mỗi dòng chỉ thuộc 1 người.
   - **Copy TRANSCRIPT** → dán vào chat mới của Claude Project: `/analyze` + `context:` + `notes:`.
3. Tab **NƯỚC ĐI**: dán khối MOVES (từ `/qcmoves`) → **KIỂM TRA**. Dòng đỏ = anchor sai/không tìm thấy (sửa câu trích hoặc dùng `at mm:ss.s`). Double-click cột **Badge** để đổi vị trí (auto → L → C → R). **Xem trước thẻ** để xem 1 nước.
4. Tab **DRAFT**: **SINH DRAFT CAPCUT** (đóng CapCut trước nếu ghi đè draft cũ). Xong: mở CapCut → draft cùng tên → xem, chỉnh, export. **Copy CHAPTERS** → `/metadata chapters: …`.
5. Tab **THUMBNAIL**: dán kết quả `/thumbnail` → **TẠO THUMBNAIL** → `thumbs/A1.png…C3.png`.

### Cài đặt hay chỉnh (tab KÊNH)
- **Tốc độ gõ** (mặc định 30 ký tự/giây) và **Dừng đọc** (2.5 giây sau khi gõ xong).
- **Giữ Game Review** (15 giây — đủ chỗ cho end screen YouTube).
- **Model Whisper**: `large-v3` (cần ~6 GB VRAM). Card yếu hơn: `medium`.
- **Font**: trống = Arial/Arial Bold của Windows.

## Draft CapCut gồm gì
- Track **main**: clip gốc cắt tại mỗi nước + đoạn dừng hình đã render (khung hình đứng tối nhẹ, badge bật lên, thanh eval trượt, thẻ trắng chữ đen gõ dần, "Better was", tag, dừng đọc) + Game Review cuối.
- Track **bar**: thanh eval khi clip đang chạy. Track **players**: 2 ô player.
- Track **subs**: phụ đề thoại (chữ trắng viền đen, sửa được trong CapCut — chọn hết để đổi font/cỡ).
- Track **sfx_***: âm badge theo loại nước, tiếng gõ phím, checkmate.

Sửa chữ trên thẻ: sửa khối MOVES trong app rồi sinh lại draft (thẻ là video đã render, không phải chữ CapCut).

## Lỗi thường gặp
| Triệu chứng | Cách sửa |
|---|---|
| Kiểm tra báo torch **KHÔNG thấy GPU** | Torch bản CPU bị cài đè. Chạy: `venv\Scripts\python -m pip install --force-reinstall torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128` |
| `Could not locate cudnn_ops64_9.dll` / lỗi cuDNN | App đã tự thêm thư mục DLL của torch; nếu vẫn lỗi, cài lại CUDA Toolkit 12.8 và driver mới |
| Lỗi `torchcodec` / thiếu `avcodec-*.dll` | Dùng FFmpeg bản **shared 7.x** (bước 2), mở lại app |
| pyannote báo 401/403, "Agree" | Chưa bấm Agree ở trang model (bước 5) hoặc token sai |
| Ai nói bị gán nhầm nhiều | Điền **Số người nói tối thiểu/tối đa** ở tab THOẠI; dùng phụ đề chuẩn .srt; tick Demucs |
| CapCut không thấy draft | Sai "Thư mục draft CapCut" (mặc định `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft`); khởi động lại CapCut |
| Anchor đỏ "không tìm thấy câu trích" | Câu trích trong MOVES khác chữ trong transcript → sửa câu trích cho khớp, hoặc dùng `at mm:ss.s` |
