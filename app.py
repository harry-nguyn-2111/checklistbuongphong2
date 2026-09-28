import os
import tempfile
from datetime import datetime
from urllib.parse import unquote, urlparse

import pandas as pd
import pymysql
import streamlit as st

st.set_page_config(page_title="Housekeeping Checklist", page_icon="🏨", layout="wide")

# ============================================================
# HOUSEKEEPING CHECKLIST
# MySQL / Aiven persistence
# ============================================================

rooms = [f"Phòng {i}" for i in range(101, 111)]

checklist = {
    "Công việc chính": [
        "Dọn phòng", "Thay ga giường", "Thay vỏ gối", "Thay khăn",
        "Hút bụi sàn", "Lau sàn", "Lau bụi nội thất",
        "Kiểm tra mùi phòng", "Kiểm tra điều hòa", "Kiểm tra đèn điện",
    ],
    "Đồ đạc trong phòng": [
        "Mini bar", "Giường", "Tủ quần áo", "Bàn", "Ghế", "Rèm cửa",
        "Tivi", "Điện thoại", "Két an toàn", "Ấm đun nước", "Máy sấy tóc",
    ],
    "Đồ vệ sinh cá nhân": [
        "Dầu gội", "Sữa tắm", "Xà phòng", "Bàn chải đánh răng",
        "Kem đánh răng", "Mũ tắm", "Dao cạo râu", "Giấy vệ sinh",
        "Khăn mặt", "Khăn tay", "Khăn tắm",
    ],
    "Phòng tắm": [
        "Bồn cầu sạch", "Lavabo sạch", "Gương sạch", "Vòi sen sạch",
        "Sàn phòng tắm sạch", "Thùng rác", "Không có tóc/rác",
        "Kiểm tra nước nóng", "Kiểm tra thoát nước",
    ],
    "Kiểm tra cuối": [
        "Kiểm tra tài sản khách", "Kiểm tra đồ thất lạc",
        "Kiểm tra cửa phòng", "Kiểm tra khóa cửa",
        "Tắt các thiết bị không cần thiết", "Đóng cửa sổ",
        "Xịt khử mùi", "Phòng sẵn sàng đón khách",
    ],
}

TOTAL_ITEMS = sum(len(v) for v in checklist.values())

st.markdown(
    """
    <style>
    .main-title {background:#1f4e79;color:white;padding:20px;border-radius:12px;
    text-align:center;margin-bottom:20px}
    .room-card {background:white;border:1px solid #e5e7eb;border-radius:12px;
    padding:12px;margin-bottom:8px}
    </style>
    """,
    unsafe_allow_html=True,
)

# ============================================================
# DATABASE CONFIGURATION
# Supports Streamlit secrets, environment variables and Aiven URI.
# ============================================================

def secret_value(section, key, default=None):
    """Read [mysql] key from Streamlit secrets without crashing if secrets are absent."""
    try:
        if section in st.secrets and key in st.secrets[section]:
            return st.secrets[section][key]
    except Exception:
        pass
    return default


def get_setting(key, default=None):
    """Environment variable first, then [mysql] Streamlit secret."""
    value = os.getenv(key)
    if value:
        return value
    return secret_value("mysql", key.lower(), default)


def get_database_config():
    """Build a connection config from MYSQL_URL or individual Aiven settings."""
    mysql_url = os.getenv("MYSQL_URL") or os.getenv("AIVEN_MYSQL_URL") or secret_value("mysql", "url")

    if mysql_url:
        parsed = urlparse(mysql_url)
        if not parsed.hostname:
            raise ValueError("MYSQL_URL/AIVEN_MYSQL_URL không hợp lệ.")

        database = parsed.path.lstrip("/") or "defaultdb"
        query = dict(item.split("=", 1) for item in parsed.query.split("&") if "=" in item)
        ssl_mode = query.get("ssl-mode", query.get("ssl_mode", "REQUIRED")).upper()

        return {
            "host": parsed.hostname,
            "port": parsed.port or 3306,
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "database": database,
            "ssl_mode": ssl_mode,
            "ssl_ca": os.getenv("MYSQL_SSL_CA") or secret_value("mysql", "ssl_ca"),
        }

    host = get_setting("MYSQL_HOST")
    port = get_setting("MYSQL_PORT", 3306)
    user = get_setting("MYSQL_USER", "avnadmin")
    password = get_setting("MYSQL_PASSWORD")
    database = get_setting("MYSQL_DATABASE", "defaultdb")
    ssl_mode = str(get_setting("MYSQL_SSL_MODE", "REQUIRED")).upper()
    ssl_ca = get_setting("MYSQL_SSL_CA")

    if not host or not password:
        raise RuntimeError(
            "Chưa cấu hình MySQL/Aiven. Hãy thêm [mysql] vào Streamlit Secrets "
            "hoặc cấu hình MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE."
        )

    return {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "database": database,
        "ssl_mode": ssl_mode,
        "ssl_ca": ssl_ca,
    }


def prepare_ssl(ssl_mode, ssl_ca):
    """Create PyMySQL SSL options. Aiven recommends TLS; VERIFY_CA/VERIFY_IDENTITY need CA."""
    if ssl_mode in {"DISABLED", "OFF"}:
        return None

    if ssl_ca:
        # Streamlit Cloud secrets can contain the PEM itself.
        if "BEGIN CERTIFICATE" in str(ssl_ca):
            ca_file = os.path.join(tempfile.gettempdir(), "aiven_mysql_ca.pem")
            with open(ca_file, "w", encoding="utf-8") as f:
                f.write(str(ssl_ca))
            return {"ca": ca_file}
        return {"ca": str(ssl_ca)}

    # REQUIRED encrypts the connection. For certificate verification, provide ssl_ca.
    return {}


def get_connection():
    config = get_database_config()
    ssl_options = prepare_ssl(config["ssl_mode"], config["ssl_ca"])

    kwargs = {
        "host": config["host"],
        "port": config["port"],
        "user": config["user"],
        "password": config["password"],
        "database": config["database"],
        "charset": "utf8mb4",
        "connect_timeout": 10,
        "read_timeout": 20,
        "write_timeout": 20,
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": True,
    }

    if ssl_options is not None:
        kwargs["ssl"] = ssl_options

    return pymysql.connect(**kwargs)


def init_database():
    """Create the two tables used by the app if they do not exist."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS housekeeping_tasks (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    room VARCHAR(50) NOT NULL,
                    category VARCHAR(100) NOT NULL,
                    item VARCHAR(255) NOT NULL,
                    checked TINYINT(1) NOT NULL DEFAULT 0,
                    updated_by VARCHAR(150) NULL,
                    updated_at DATETIME NOT NULL,
                    UNIQUE KEY uq_room_category_item (room, category, item),
                    INDEX idx_room (room)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS housekeeping_history (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    room VARCHAR(50) NOT NULL,
                    employee VARCHAR(150) NOT NULL,
                    completed_items INT NOT NULL,
                    total_items INT NOT NULL,
                    progress DECIMAL(5,2) NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    created_at DATETIME NOT NULL,
                    INDEX idx_history_room (room),
                    INDEX idx_history_created (created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """
            )
    finally:
        conn.close()


def load_room_tasks(room):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT category, item, checked FROM housekeeping_tasks WHERE room=%s",
                (room,),
            )
            return cursor.fetchall()
    finally:
        conn.close()


def save_room_tasks(room, employee):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            for category_index, (category, items) in enumerate(checklist.items()):
                for item_index, item in enumerate(items):
                    checked = 1 if st.session_state.get(
                        task_key(room, category_index, item_index), False
                    ) else 0
                    cursor.execute(
                        """
                        INSERT INTO housekeeping_tasks
                            (room, category, item, checked, updated_by, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            checked=VALUES(checked),
                            updated_by=VALUES(updated_by),
                            updated_at=VALUES(updated_at)
                        """,
                        (room, category, item, checked, employee or None, now),
                    )
    finally:
        conn.close()


def save_completion_history(room, employee, done, progress):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO housekeeping_history
                    (room, employee, completed_items, total_items, progress, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    room,
                    employee,
                    done,
                    TOTAL_ITEMS,
                    round(progress * 100, 2),
                    "Hoàn thành",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
    finally:
        conn.close()


def load_history():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, room AS `Phòng`, employee AS `Nhân viên`,
                       completed_items AS `Đã hoàn thành`,
                       total_items AS `Tổng checklist`,
                       progress AS `Tiến độ (%)`, status AS `Trạng thái`,
                       created_at AS `Thời gian`
                FROM housekeeping_history
                ORDER BY created_at DESC
                LIMIT 500
                """
            )
            return cursor.fetchall()
    finally:
        conn.close()


def task_key(room, category_index, item_index):
    safe_room = room.replace(" ", "_")
    return f"hk_{safe_room}_{category_index}_{item_index}"


def load_room_into_session(room):
    """Load DB values only when switching rooms, so checkbox changes are not overwritten."""
    if st.session_state.get("loaded_room") == room:
        return

    rows = load_room_tasks(room)
    db_values = {(row["category"], row["item"]): bool(row["checked"]) for row in rows}

    for category_index, (category, items) in enumerate(checklist.items()):
        for item_index, item in enumerate(items):
            st.session_state[task_key(room, category_index, item_index)] = db_values.get(
                (category, item), False
            )

    st.session_state["loaded_room"] = room


def room_progress(room):
    done = 0
    for category_index, items in enumerate(checklist.values()):
        for item_index in range(len(items)):
            if st.session_state.get(task_key(room, category_index, item_index), False):
                done += 1
    return done, done / TOTAL_ITEMS if TOTAL_ITEMS else 0


# ============================================================
# DATABASE STATUS
# ============================================================
try:
    init_database()
    db_ready = True
except Exception as exc:
    db_ready = False
    db_error = str(exc)

# ============================================================
# UI
# ============================================================
st.markdown(
    """
    <div class="main-title">
    <h1>🏨 HOUSEKEEPING CHECKLIST</h1>
    <p>Kiểm tra công việc nhân viên buồng phòng</p>
    </div>
    """,
    unsafe_allow_html=True,
)

if db_ready:
    st.success("🟢 Đã kết nối MySQL / Aiven và sẵn sàng lưu dữ liệu.")
else:
    st.error(f"🔴 Chưa kết nối được MySQL / Aiven: {db_error}")
    st.info("Bạn có thể cấu hình thông tin kết nối trong Streamlit Secrets theo hướng dẫn bên dưới.")

employee = st.text_input("👤 Tên nhân viên", placeholder="Nhập tên nhân viên...")

selected = st.selectbox(
    "🚪 Chọn phòng",
    rooms,
    index=0,
)

if db_ready:
    try:
        load_room_into_session(selected)
    except Exception as exc:
        st.error(f"Không tải được dữ liệu phòng từ MySQL: {exc}")

st.subheader(f"🛏️ {selected}")

for category_index, (category, items) in enumerate(checklist.items()):
    st.markdown(f"### 📌 {category}")
    for item_index, item in enumerate(items):
        st.checkbox(
            item,
            key=task_key(selected, category_index, item_index),
        )

done, progress = room_progress(selected)

c1, c2, c3 = st.columns(3)
c1.metric("Đã hoàn thành", f"{done}/{TOTAL_ITEMS}")
c2.metric("Tiến độ", f"{progress * 100:.0f}%")
c3.metric(
    "Trạng thái",
    "✓ Hoàn thành" if progress == 1 else ("Đang làm" if progress > 0 else "Chưa hoàn thành"),
)
st.progress(progress)

b1, b2 = st.columns(2)

with b1:
    if st.button("💾 Lưu tiến độ vào MySQL", use_container_width=True):
        if not db_ready:
            st.error("Chưa có kết nối MySQL / Aiven.")
        elif not employee.strip():
            st.warning("⚠️ Vui lòng nhập tên nhân viên.")
        else:
            try:
                save_room_tasks(selected, employee.strip())
                st.success(f"Đã lưu tiến độ {selected} lên MySQL/Aiven.")
            except Exception as exc:
                st.error(f"Lỗi khi lưu MySQL: {exc}")

with b2:
    if st.button("✓ Hoàn thành phòng", type="primary", use_container_width=True):
        if not db_ready:
            st.error("Chưa có kết nối MySQL / Aiven.")
        elif not employee.strip():
            st.warning("⚠️ Vui lòng nhập tên nhân viên.")
        elif progress < 1:
            st.warning("⚠️ Phòng chưa hoàn thành tất cả checklist.")
        else:
            try:
                save_room_tasks(selected, employee.strip())
                save_completion_history(selected, employee.strip(), done, progress)
                st.success(f"🎉 {selected} đã hoàn thành và đã lưu vào MySQL/Aiven!")
            except Exception as exc:
                st.error(f"Lỗi khi lưu hoàn thành phòng: {exc}")

st.markdown("---")
st.subheader("🏨 Tổng quan 10 phòng")

cols = st.columns(5)
for i, room in enumerate(rooms):
    if db_ready:
        try:
            load_room_into_session(room)
        except Exception:
            pass

    done_i, progress_i = room_progress(room)
    with cols[i % 5]:
        st.markdown(
            f'<div class="room-card"><strong>{room}</strong></div>',
            unsafe_allow_html=True,
        )
        st.progress(progress_i)
        st.caption(f"{done_i}/{TOTAL_ITEMS} — {progress_i * 100:.0f}%")

# Restore selected room as the active room after overview loading.
st.session_state["loaded_room"] = selected

st.markdown("---")
st.subheader("📋 Nhật ký Housekeeping từ MySQL / Aiven")

if db_ready:
    try:
        history_rows = load_history()
        if history_rows:
            history_df = pd.DataFrame(history_rows)
            st.dataframe(history_df, use_container_width=True, hide_index=True)
        else:
            st.info("Chưa có lịch sử Housekeeping.")
    except Exception as exc:
        st.error(f"Không thể đọc lịch sử MySQL: {exc}")
else:
    st.info("Nhật ký sẽ hiển thị sau khi kết nối MySQL/Aiven thành công.")

with st.expander("⚙️ Hướng dẫn cấu hình MySQL / Aiven"):
    st.markdown(
        """
        ### Cách 1 — Streamlit Cloud

        Vào **App → Settings → Secrets** và thêm:

        ```toml
        [mysql]
        host = "YOUR_AIVEN_HOST"
        port = 12345
        user = "avnadmin"
        password = "YOUR_PASSWORD"
        database = "defaultdb"
        ssl_mode = "REQUIRED"
        ```

        Nếu muốn xác minh chứng chỉ Aiven, thêm `ssl_ca` bằng nội dung CA certificate.

        ### Cách 2 — dùng Aiven Service URI

        ```toml
        [mysql]
        url = "mysql://avnadmin:YOUR_PASSWORD@YOUR_HOST:YOUR_PORT/defaultdb?ssl-mode=REQUIRED"
        ```

        **Không đưa password thật vào GitHub.** Hãy dùng Streamlit Secrets hoặc biến môi trường.
        """
    )
