import streamlit as st
import gspread
import pandas as pd
import random
import os
import base64
from itertools import product
from datetime import datetime, timezone, timedelta

# --- 定数定義 ---
SPREADSHEET_KEY = "14sDX_7rw3WcGpWji59Ornhkx9G9obs-ZRn8sgqcs9yA"
NATION_SHEET = "国家マスタ"
EXECUTIVE_SHEET = "重役マスタ"
CONTRACT_SHEET = "初期契約マスタ"
SCORE_SHEET = "スコア記録"
PRESET_SHEET = "プリセット"
BALANCE_SHEET = "バランス調整履歴"
IMAGE_DIR = "images"


# --- スプレッドシート操作 ---
@st.cache_resource(ttl=1800)
def get_gspread_client():
    """gspreadクライアントを取得する（キャッシュ活用）"""
    return gspread.service_account_from_dict(st.secrets["gcp_service_account"])


def get_score_sheet():
    """スコア記録シートのワークシートオブジェクトを取得する"""
    gc = get_gspread_client()
    sh = gc.open_by_key(SPREADSHEET_KEY)
    return sh.worksheet(SCORE_SHEET)


def save_draft_to_sheet(
    player_count, draft_order, draft_results, first_round_order, draft_method, board
):
    """ドラフト結果をスプレッドシートに保存する"""
    try:
        worksheet = get_score_sheet()
        jst = timezone(timedelta(hours=+9), "JST")
        timestamp = datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S")
        game_id = int(datetime.now(jst).timestamp())

        # シートからヘッダーを取得
        all_values = worksheet.get_all_values()
        if not all_values:
            header = [
                "GameID",
                "Timestamp",
                "PlayerCount",
                "PlayerName",
                "TurnOrder1R",
                "DraftMethod",
                "Nation",
                "Executive",
                "Contract",
                "InitialScore",
                "FinalScore",
                "Board",
            ]
            worksheet.append_row(header, value_input_option="USER_ENTERED")
        else:
            header = all_values[0]

        rows_to_append = []
        # auction draft uses a different draft order
        player_list = draft_order if draft_method == "normal" else first_round_order
        for player_name in player_list:
            result = draft_results[player_name]
            turn_order = first_round_order.index(player_name) + 1
            # In auction mode, VP is deducted, not set to 0
            initial_score = (
                10 if draft_method == "normal" else 10 - result.get("bid", 0)
            )

            # データを辞書として作成
            data_dict = {
                "GameID": game_id,
                "Timestamp": timestamp,
                "PlayerCount": player_count,
                "PlayerName": player_name,
                "TurnOrder1R": turn_order,
                "DraftMethod": draft_method,
                "Nation": result["nation"],
                "Executive": result["executive"],
                "Contract": result["contract"],
                "InitialScore": initial_score,
                "FinalScore": "",
                "Board": board,
            }

            # ヘッダーの順番に合わせてリストを作成
            row = [data_dict.get(h, "") for h in header]
            rows_to_append.append(row)

        # 実質的な最終行を特定する（GameIDが空でない最後の行）
        last_data_row = 1  # デフォルトはヘッダー行
        for i, row in enumerate(all_values):
            if row and len(row) > 0 and str(row[0]).strip() != "":
                last_data_row = i + 1

        next_row = last_data_row + 1

        # 指定した行番号から書き込む
        worksheet.update(
            range_name=f"A{next_row}",
            values=rows_to_append,
            value_input_option="USER_ENTERED",
        )
        return game_id
    except Exception as e:
        st.error(f"スプレッドシートへの書き込み中にエラーが発生しました: {e}")
        return None


@st.cache_data(ttl=60)  # 1分キャッシュ
def load_latest_game_from_sheet():
    """スコアが未入力の最新のゲームデータをシートから読み込む"""
    try:
        worksheet = get_score_sheet()
        # get_all_records()はヘッダーに重複（空文字含む）があるとエラーになるため、get_all_values()を使用する
        all_values = worksheet.get_all_values()
        if not all_values or len(all_values) < 2:
            return None

        headers = all_values[0]
        rows = all_values[1:]
        df = pd.DataFrame(rows, columns=headers)

        if "FinalScore" not in df.columns:
            return None

        # GameIDを数値型に変換（エラー回避）
        if "GameID" in df.columns:
            df["GameID"] = pd.to_numeric(df["GameID"], errors="coerce")
            df = df.dropna(subset=["GameID"])

        unscored_games = df[df["FinalScore"].astype(str).str.strip() == ""]
        if unscored_games.empty:
            return None

        latest_game_id = unscored_games["GameID"].max()
        latest_game_df = unscored_games[
            unscored_games["GameID"] == latest_game_id
        ].copy()

        return latest_game_df.to_dict("records")
    except Exception as e:
        st.error(f"ゲームデータの読み込み中にエラーが発生しました: {e}")
        return None


def delete_game_from_sheet(game_id):
    """指定されたGameIDのデータをシートから削除する"""
    try:
        worksheet = get_score_sheet()
        all_values = worksheet.get_all_values()
        if not all_values:
            return False

        headers = all_values[0]
        try:
            game_id_col_idx = headers.index("GameID")
        except ValueError:
            return False

        rows_to_delete = []
        # GameIDの比較用文字列を作成（floatの.0対策）
        try:
            target_id_str = str(int(float(game_id)))
        except (ValueError, TypeError):
            target_id_str = str(game_id)

        # Row 1 in sheet is all_values[0].
        # We need 1-based index for delete_rows.
        for i, row in enumerate(all_values):
            if i == 0:
                continue
            if len(row) > game_id_col_idx:
                cell_val = str(row[game_id_col_idx]).strip()
                # セル側も同様に処理（念のため）
                try:
                    cell_val_norm = str(int(float(cell_val)))
                except (ValueError, TypeError):
                    cell_val_norm = cell_val

                if cell_val_norm == target_id_str:
                    rows_to_delete.append(i + 1)

        if not rows_to_delete:
            return False

        # 下から順に削除しないと行番号がずれる
        rows_to_delete.sort(reverse=True)
        for row_num in rows_to_delete:
            worksheet.delete_rows(row_num)

        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"データの削除中にエラーが発生しました: {e}")
        return False


@st.cache_data(ttl=60)
def get_recent_usage_counts(limit=10):
    """直近のゲーム（指定数）で使用された国家・重役の出現回数を取得する"""
    try:
        worksheet = get_score_sheet()
        all_values = worksheet.get_all_values()
        if not all_values or len(all_values) < 2:
            return {}, {}

        headers = all_values[0]
        df = pd.DataFrame(all_values[1:], columns=headers)

        if (
            "GameID" not in df.columns
            or "Nation" not in df.columns
            or "Executive" not in df.columns
        ):
            return {}, {}

        # GameIDのユニーク値を出現順に取得
        # (スプレッドシートは追記型なので、下の行ほど新しいと仮定)
        unique_games = df["GameID"].unique()
        recent_games = unique_games[-limit:]

        recent_df = df[df["GameID"].isin(recent_games)]

        nation_counts = recent_df["Nation"].value_counts().to_dict()
        exec_counts = recent_df["Executive"].value_counts().to_dict()

        return nation_counts, exec_counts
    except Exception as e:
        # エラー時は空の辞書を返して、重み付けなし（通常のランダム）として動作させる
        return {}, {}


@st.cache_data(ttl=60)
def get_last_game_players():
    """最後にプレイされたゲームのプレイヤー名リストを取得する"""
    try:
        worksheet = get_score_sheet()
        all_values = worksheet.get_all_values()
        if not all_values or len(all_values) < 2:
            return []

        headers = all_values[0]
        df = pd.DataFrame(all_values[1:], columns=headers)

        if "GameID" not in df.columns or "PlayerName" not in df.columns:
            return []

        # GameIDを数値に変換（エラーは除去）
        df["GameID"] = pd.to_numeric(df["GameID"], errors="coerce")
        df = df.dropna(subset=["GameID"])

        if df.empty:
            return []

        # GameIDが最大のものを最新とする
        last_game_id = df["GameID"].max()
        last_game_df = df[df["GameID"] == last_game_id]

        return last_game_df["PlayerName"].tolist()
    except:
        return []


@st.cache_data(ttl=60)
def get_preset_data():
    """プリセットシートからデータを読み込む"""
    try:
        sh = get_gspread_client().open_by_key(SPREADSHEET_KEY)
        try:
            ws = sh.worksheet(PRESET_SHEET)
        except gspread.WorksheetNotFound:
            return {}

        data = ws.get_all_records()
        presets = {}
        for row in data:
            name = str(row.get("PresetName", "")).strip()
            if name:
                # PlayerCountが空や不正な場合はデフォルト4
                try:
                    p_count = int(row.get("PlayerCount", 4))
                except:
                    p_count = 4

                presets[name] = {
                    "nations": [
                        x.strip()
                        for x in str(row.get("Nations", "")).split(",")
                        if x.strip()
                    ],
                    "executives": [
                        x.strip()
                        for x in str(row.get("Executives", "")).split(",")
                        if x.strip()
                    ],
                    "count": p_count,
                    "board": str(row.get("Board", "通常")),
                    "is_default": str(row.get("IsDefault", "")).upper()
                    in ["TRUE", "1", "YES"],
                }
        return presets
    except Exception as e:
        return {}


def set_default_preset(target_name):
    """指定したプリセットをデフォルトに設定する"""
    try:
        sh = get_gspread_client().open_by_key(SPREADSHEET_KEY)
        ws = sh.worksheet(PRESET_SHEET)

        # Ensure column exists
        headers = ws.row_values(1)
        if "IsDefault" not in headers:
            # グリッドサイズが足りない場合は拡張
            if len(headers) >= ws.col_count:
                ws.resize(cols=len(headers) + 1)
            ws.update_cell(1, len(headers) + 1, "IsDefault")
            headers.append("IsDefault")

        col_idx = headers.index("IsDefault") + 1
        name_col_idx = headers.index("PresetName") + 1

        all_values = ws.get_all_values()

        cells_to_update = []
        for i, row in enumerate(all_values):
            if i == 0:
                continue

            row_num = i + 1
            # 行の長さが足りない場合のガード
            if len(row) < name_col_idx:
                continue
                
            current_name = row[name_col_idx - 1]
            val = "TRUE" if current_name == target_name else "FALSE"

            cells_to_update.append(gspread.Cell(row_num, col_idx, val))

        if cells_to_update:
            ws.update_cells(cells_to_update)
            
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(str(e))
        return False


def save_preset_data(name, nations, execs, count, board):
    """現在の選択状態をプリセットとして保存する"""
    try:
        sh = get_gspread_client().open_by_key(SPREADSHEET_KEY)
        try:
            ws = sh.worksheet(PRESET_SHEET)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=PRESET_SHEET, rows=100, cols=5)
            ws.append_row(
                ["PresetName", "Nations", "Executives", "PlayerCount", "Board"]
            )

        # ヘッダー確認と追加（既存シートへのカラム追加）
        headers = ws.row_values(1)
        if "PlayerCount" not in headers:
            if len(headers) >= ws.col_count:
                ws.resize(cols=len(headers) + 1)
            ws.update_cell(1, len(headers) + 1, "PlayerCount")
            headers.append("PlayerCount")
        if "Board" not in headers:
            if len(headers) >= ws.col_count:
                ws.resize(cols=len(headers) + 1)
            ws.update_cell(1, len(headers) + 1, "Board")
            headers.append("Board")

        row = [name, ",".join(nations), ",".join(execs), count, board]
        ws.append_row(row)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"プリセット保存中にエラー: {e}")
        return False


@st.cache_data(ttl=60)
def get_balance_log():
    """バランス調整履歴を取得する"""
    try:
        sh = get_gspread_client().open_by_key(SPREADSHEET_KEY)
        try:
            ws = sh.worksheet(BALANCE_SHEET)
        except gspread.WorksheetNotFound:
            return []

        records = ws.get_all_records()
        return records
    except Exception:
        return []


def add_balance_log(date_str, version, note):
    """バランス調整履歴を追加する（同日・同バージョンなら追記）"""
    try:
        sh = get_gspread_client().open_by_key(SPREADSHEET_KEY)
        try:
            ws = sh.worksheet(BALANCE_SHEET)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=BALANCE_SHEET, rows=100, cols=3)
            ws.append_row(["Date", "Version", "Note"])

        all_values = ws.get_all_values()
        target_row_idx = None
        current_note = ""

        # Date=0, Version=1, Note=2
        for i, row in enumerate(all_values):
            if i == 0:
                continue
            if (
                len(row) > 1
                and str(row[0]) == str(date_str)
                and str(row[1]) == str(version)
            ):
                target_row_idx = i + 1  # 1-based index
                current_note = row[2] if len(row) > 2 else ""
                break

        if target_row_idx:
            # 既存行に追記
            new_note = current_note + "\n" + note if current_note else note
            ws.update_cell(target_row_idx, 3, new_note)
        else:
            # 新規追加
            ws.append_row([date_str, version, note])

        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"履歴保存中にエラー: {e}")
        return False


def save_master_update(sheet_name, data_dict):
    """マスタデータを追記保存する"""
    try:
        sh = get_gspread_client().open_by_key(SPREADSHEET_KEY)
        ws = sh.worksheet(sheet_name)

        # ヘッダー確認（EffectiveDateがなければ追加）
        headers = ws.row_values(1)
        if "EffectiveDate" not in headers:
            if len(headers) >= ws.col_count:
                ws.resize(cols=len(headers) + 1)
            ws.update_cell(1, len(headers) + 1, "EffectiveDate")
            headers.append("EffectiveDate")

        # データをヘッダー順に並べる
        row = [data_dict.get(h, "") for h in headers]
        ws.append_row(row)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"マスタ保存中にエラー: {e}")
        return False


def update_scores_in_sheet(game_id, player_scores):
    """指定されたGameIDのスコアを更新する"""
    try:
        worksheet = get_score_sheet()

        # ヘッダーを取得して列番号を動的に特定
        header = worksheet.row_values(1)
        game_id_col = header.index("GameID") + 1
        player_name_col = header.index("PlayerName") + 1
        final_score_col = header.index("FinalScore") + 1

        cell_list = worksheet.findall(str(game_id), in_column=game_id_col)

        for cell in cell_list:
            row_num = cell.row
            player_name_in_sheet = worksheet.cell(row_num, player_name_col).value
            if player_name_in_sheet in player_scores:
                score = player_scores[player_name_in_sheet]
                worksheet.update_cell(row_num, final_score_col, score)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"スコアの更新中にエラーが発生しました: {e}")
        return False


# --- データ読み込みとキャッシュ ---
@st.cache_data(ttl=1800)
def get_master_data(worksheet_name):
    """指定されたワークシートからデータを読み込み、DataFrameとして返す"""
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(SPREADSHEET_KEY)
        worksheet = sh.worksheet(worksheet_name)
        data = worksheet.get_all_values()
        if len(data) < 2:
            return None
        headers = data[0]
        df_data = data[1:]
        df = pd.DataFrame(df_data, columns=headers)

        # バージョン管理（EffectiveDateがある場合、最新のみを返す）
        if "EffectiveDate" in df.columns:
            # 日付型に変換
            df["EffectiveDate"] = pd.to_datetime(df["EffectiveDate"], errors="coerce")
            # 今日以前のデータのみ対象
            today = datetime.now()
            df = df[
                (df["EffectiveDate"] <= today) | (pd.isna(df["EffectiveDate"]))
            ]
            # 日付昇順ソートして、同じ名前なら最後の行（最新）を採用
            df = df.sort_values("EffectiveDate")
            df = df.drop_duplicates(subset=["Name"], keep="last")

        return df
    except Exception as e:
        st.error(f"データ読み込み中にエラーが発生しました: {e}")
        return None


def image_to_data_url(filepath: str) -> str:
    """画像ファイルを読み込み、Base64エンコードされたデータURLに変換する。"""
    try:
        with open(filepath, "rb") as f:
            img_bytes = f.read()
        b64_bytes = base64.b64encode(img_bytes).decode()
        ext = filepath.split(".")[-1].lower()
        mime_type = (
            f"image/{ext}"
            if ext in ["png", "jpeg", "jpg", "gif", "svg"]
            else "image/png"
        )
        return f"data:{mime_type};base64,{b64_bytes}"
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


# --- セッション管理 ---
def initialize_session_state():
    """セッション変数を初期化する"""
    if "screen" not in st.session_state:
        st.session_state.screen = "landing"

    if "game_setup" not in st.session_state:
        st.session_state.game_setup = {}

    if "active_game" not in st.session_state:
        st.session_state.active_game = None


def reset_game_setup():
    """進行中のゲームセットアップ情報をリセットする"""
    st.session_state.game_setup = {
        "player_count": 4,
        "player_names": [],
        "draft_candidate_count_option": "人数と同じ",
        "selected_nations": [],
        "selected_executives": [],
        "draft_order": [],
        "nation_exec_candidates": [],
        "contract_candidates": [],
        "draft_results": {},
        "draft_method": "",
        "draft_turn_index": 0,
        "current_selection_ne": None,
        "current_selection_contract": None,
        "board": "通常",
        # --- Auction State ---
        "auction_board": {},  # {1: {'player': 'A', 'bid': 2}, 2: ...}
        "auction_player_status": {},  # {'A': 'placed', 'B': 'displaced'}
        "auction_log": [],
        "auction_phase": "bidding",  # bidding or drafting
    }
    # プレイヤー名の入力欄をリセット
    for key in list(st.session_state.keys()):
        if key.startswith("player_"):
            del st.session_state[key]

    # --- デフォルトプリセットの適用 ---
    # マスタデータ取得
    nation_df = get_master_data(NATION_SHEET)
    exec_df = get_master_data(EXECUTIVE_SHEET)

    all_nations = nation_df["Name"].tolist() if nation_df is not None else []
    all_execs = exec_df["Name"].tolist() if exec_df is not None else []

    # デフォルト値
    current_nations = all_nations
    current_execs = all_execs
    current_count = 4
    current_board = "通常"
    current_preset_name = ""

    # プリセット取得
    presets = get_preset_data()
    def_name = next((k for k, v in presets.items() if v.get("is_default")), None)

    if def_name:
        p = presets[def_name]
        current_nations = [n for n in p["nations"] if n in all_nations]
        current_execs = [e for e in p["executives"] if e in all_execs]
        current_count = p.get("count", 4)
        current_board = p.get("board", "通常")
        current_preset_name = def_name

    # Session Stateにセット（上書き）
    st.session_state.ms_nations = current_nations
    st.session_state.ms_executives = current_execs
    st.session_state.num_player_count = current_count
    st.session_state.board_type_selection = current_board
    st.session_state.preset_selector = current_preset_name

    # プレイヤー名の初期化（履歴から）
    last_players = get_last_game_players()
    for idx, name in enumerate(last_players):
        if idx < 5:
            st.session_state[f"player_{idx}"] = name


# --- 画面描画関数 ---


def check_and_handle_auction_end(setup_data):
    """オークションの終了条件をチェックし、終了していれば状態を更新する"""
    # Check if any player is still bidding or displaced
    for player_status in setup_data["auction_player_status"].values():
        if player_status["status"] != "placed":
            return  # Auction is not over

    # --- If we reach here, the auction is over ---
    setup_data["auction_phase"] = "drafting"
    setup_data["draft_turn_index"] = 0  # Reset for drafting phase

    # Create final turn order list
    final_order = [None] * setup_data["player_count"]
    for i in range(1, setup_data["player_count"] + 1):
        # Handle cases where a turn order spot might not be filled (unlikely in normal flow)
        if i in setup_data["auction_board"]:
            player_name = setup_data["auction_board"][i]["player"]
            final_order[i - 1] = player_name

    setup_data["final_turn_order"] = final_order
    setup_data["auction_draft_order"] = list(reversed(final_order))

    log_message = "全員の入札が確定しました。オークション終了！ドラフトを開始します。"
    setup_data["auction_log"].insert(0, log_message)


def show_landing_screen():
    """アプリ起動時の初期画面"""
    st.title("バラージ セットアップ & スコア管理")

    col1, col2 = st.columns([0.7, 0.3])
    with col2:
        if st.button("最新の情報に更新", use_container_width=True):
            st.cache_data.clear()
            st.session_state.active_game = None
            st.rerun()

    latest_game = st.session_state.active_game
    if latest_game:
        with st.container(border=True):
            st.subheader("スコア入力待ちのゲームがあります")
            game_time = latest_game[0]["Timestamp"]
            draft_method_jp = (
                "通常ドラフト"
                if latest_game[0]["DraftMethod"] == "normal"
                else "オークション"
            )
            board_type = latest_game[0].get("Board", "不明")
            st.write(
                f"**ゲーム開始日時:** {game_time} ({draft_method_jp}) / **ボード:** {board_type}"
            )

            display_df = pd.DataFrame(latest_game)[
                ["PlayerName", "TurnOrder1R", "Nation", "Executive", "Contract"]
            ]
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            col_score, col_delete = st.columns([0.7, 0.3])
            with col_score:
                if st.button("スコアを入力する", type="primary", use_container_width=True):
                    st.session_state.screen = "score_input"
                    st.rerun()
            with col_delete:
                if st.button("セットアップ削除", type="secondary", use_container_width=True):
                    # シートから削除を試みる
                    game_id_to_delete = latest_game[0]["GameID"]
                    delete_game_from_sheet(game_id_to_delete)
                    st.session_state.active_game = None
                    st.cache_data.clear()
                    st.rerun()
        st.divider()

    if st.button("新規セットアップ", use_container_width=True):
        reset_game_setup()
        st.session_state.screen = "setup_form"
        st.rerun()

    if st.button("📊 統計を見る", use_container_width=True):
        st.session_state.screen = "stats"
        st.rerun()

    with st.expander("🔧 管理者メニュー"):
        col_admin1, col_admin2 = st.columns(2)
        with col_admin1:
            if st.button("マスタデータ編集（能力調整）", use_container_width=True):
                st.session_state.screen = "master_editor"
                st.rerun()

        st.divider()
        st.write("▼ バランス調整履歴の手動記録")
        with st.form("balance_log_form"):
            st.caption("マスタ編集機能を使わずに、履歴のみを記録する場合に使用します。")
            date_val = st.date_input("適用日", value=datetime.now())
            version_val = st.text_input("バージョン名 / タイトル（例: v1.1, イタリア強化）")
            note_val = st.text_area("内容メモ", height=100)

            if st.form_submit_button("記録する"):
                if version_val:
                    if add_balance_log(str(date_val), version_val, note_val):
                        st.success(f"記録しました: {version_val} ({date_val})")
                else:
                    st.warning("バージョン名を入力してください")


def show_master_editor_screen():
    """マスタデータ編集画面"""
    st.title("🔧 マスタデータ編集")

    if st.button("← 戻る"):
        st.session_state.screen = "landing"
        st.rerun()

    tab1, tab2 = st.tabs(["🏛️ 国家", "👔 重役"])

    # 共通の編集ロジック
    def render_editor(sheet_name, entity_label):
        df = get_master_data(sheet_name)
        if df is None or df.empty:
            st.error("データが読み込めませんでした")
            return

        all_names = df["Name"].tolist()
        selected_name = st.selectbox(f"編集する{entity_label}", all_names)

        if selected_name:
            # 選択された名前の最新データを取得
            current_data = df[df["Name"] == selected_name].iloc[0]

            with st.form(f"edit_form_{sheet_name}"):
                st.subheader(f"{selected_name} の編集")

                # 既存データの表示と編集
                new_desc = st.text_area(
                    "説明 (Description)", value=current_data.get("Description", "")
                )
                new_icon = st.text_input(
                    "アイコン (IconURL)", value=current_data.get("IconURL", "")
                )

                st.divider()
                st.write("▼ 更新情報")
                col1, col2 = st.columns(2)
                with col1:
                    new_date = st.date_input("適用日 (EffectiveDate)", value=datetime.now())
                with col2:
                    version_name = st.text_input("バージョン名 (例: v1.1)", placeholder="必須")

                change_note = st.text_area("変更内容メモ (バランス調整履歴に追記されます)")

                if st.form_submit_button("保存（追記）"):
                    if not version_name:
                        st.error("バージョン名を入力してください")
                        return

                    # 保存用データ作成
                    save_data = current_data.to_dict()
                    save_data["Description"] = new_desc
                    save_data["IconURL"] = new_icon
                    save_data["EffectiveDate"] = str(new_date)

                    if save_master_update(sheet_name, save_data):
                        # バランス調整ログにも記録
                        log_msg = (
                            f"[{entity_label}] {selected_name}: {change_note}"
                            if change_note
                            else f"[{entity_label}] {selected_name} 更新"
                        )
                        add_balance_log(str(new_date), version_name, log_msg)

                        st.success(f"{selected_name} を更新しました！")
                        st.balloons()
                        # 少し待ってリロード
                        import time

                        time.sleep(1)
                        st.rerun()

    with tab1:
        render_editor(NATION_SHEET, "国家")
    with tab2:
        render_editor(EXECUTIVE_SHEET, "重役")


def show_setup_form_screen(nation_df, exec_df):
    """セットアップ情報を入力する画面"""
    st.title("新規セットアップ")

    all_nations = nation_df["Name"].tolist()
    all_executives = exec_df["Name"].tolist()

    # --- Session State Initialization for Multiselect ---
    if "ms_nations" not in st.session_state:
        st.session_state.ms_nations = all_nations
    if "ms_executives" not in st.session_state:
        st.session_state.ms_executives = all_executives

    # --- Presets UI (Load) ---
    presets = get_preset_data()
    with st.expander("プリセット読み込み", expanded=False):
        col_p1, col_p2, col_p3 = st.columns([0.5, 0.25, 0.25])
        with col_p1:
            preset_options = [""] + list(presets.keys())
            selected_preset = st.selectbox(
                "プリセットを選択", preset_options, key="preset_selector"
            )
        with col_p2:
            st.write("")  # spacer
            st.write("")  # spacer
            if st.button("読み込む", use_container_width=True):
                if selected_preset and selected_preset in presets:
                    # フィルタリングして存在する要素のみをセット
                    valid_nations = [
                        n
                        for n in presets[selected_preset]["nations"]
                        if n in all_nations
                    ]
                    valid_execs = [
                        e
                        for e in presets[selected_preset]["executives"]
                        if e in all_executives
                    ]
                    st.session_state.ms_nations = valid_nations
                    st.session_state.ms_executives = valid_execs

                    # 人数とボードの設定（値があれば）
                    if "count" in presets[selected_preset]:
                        st.session_state.num_player_count = presets[selected_preset][
                            "count"
                        ]
                    if "board" in presets[selected_preset]:
                        st.session_state.board_type_selection = presets[
                            selected_preset
                        ]["board"]

                    st.success(f"プリセット '{selected_preset}' を読み込みました")
                    st.rerun()
                elif selected_preset:
                    st.warning("プリセットデータが見つかりません")
        with col_p3:
            st.write("")  # spacer
            st.write("")  # spacer
            if st.button("デフォルトに設定", use_container_width=True):
                if selected_preset:
                    if set_default_preset(selected_preset):
                        st.success(f"{selected_preset} をデフォルトに設定しました")
                        st.rerun()
                else:
                    st.warning("プリセットを選択してください")

    # --- Setup Form ---
    with st.form("initial_setup_form"):
        st.header("1. ゲーム設定")

        # Session State初期化 (ボード)
        if "board_type_selection" not in st.session_state:
            st.session_state.board_type_selection = st.session_state.game_setup.get(
                "board", "通常"
            )

        board_type = st.radio(
            "使用するボード",
            ("通常", "ナイル", "コロラド", "4・5人用"),
            key="board_type_selection",
            horizontal=True,
        )

        st.subheader("使用する国家・重役")
        # default引数はkeyがある場合は無視されるため指定しない（session_stateで管理）
        selected_nations = st.multiselect(
            "国家を選択", all_nations, key="ms_nations"
        )
        selected_executives = st.multiselect(
            "重役を選択", all_executives, key="ms_executives"
        )
        st.header("2. プレイヤー設定")
        cols = st.columns(2)
        with cols[0]:
            # Session State初期化 (プレイヤー数)
            if "num_player_count" not in st.session_state:
                st.session_state.num_player_count = st.session_state.game_setup.get(
                    "player_count", 4
                )

            player_count = st.number_input(
                "プレイヤー数",
                min_value=1,
                max_value=5,
                key="num_player_count",
            )
        with cols[1]:
            draft_options = ["人数と同じ", "人数+1", "人数+2"]
            default_draft_option = st.session_state.game_setup.get(
                "draft_candidate_count_option", "人数と同じ"
            )
            draft_candidate_count_option = st.radio(
                "ドラフト候補数",
                draft_options,
                index=draft_options.index(default_draft_option),
            )
        player_names = []
        st.subheader("プレイヤー名")

        # Session Stateにプレイヤー名がない場合、直近の履歴から補完
        if "player_0" not in st.session_state:
            last_players = get_last_game_players()
            for idx, name in enumerate(last_players):
                if idx < 5:
                    st.session_state[f"player_{idx}"] = name

        for i in range(player_count):
            player_names.append(
                st.text_input(f"プレイヤー {i+1}", key=f"player_{i}")
            )

        st.markdown("---")
        # --- Preset Save (Inside Form to capture current state) ---
        with st.expander("現在の設定をプリセット保存"):
            col_s1, col_s2 = st.columns([0.7, 0.3])
            with col_s1:
                new_preset_name = st.text_input("プリセット名")
            with col_s2:
                st.write("")  # spacer
                st.write("")  # spacer
                save_submitted = st.form_submit_button("保存", use_container_width=True)

            if save_submitted:
                if new_preset_name:
                    # form_submit_buttonなのでsession_stateは最新化されている
                    p_count = st.session_state.get("num_player_count", 4)
                    b_type = st.session_state.get("board_type_selection", "通常")

                    if save_preset_data(
                        new_preset_name,
                        st.session_state.ms_nations,
                        st.session_state.ms_executives,
                        p_count,
                        b_type,
                    ):
                        st.success(f"プリセット '{new_preset_name}' を保存しました")
                else:
                    st.warning("プリセット名を入力してください")

        st.markdown("---")
        submitted = st.form_submit_button("セットアップ実行", type="primary")
        if submitted:
            if not all(name.strip() for name in player_names):
                st.warning("すべてのプレイヤー名を入力してください。")
            else:
                st.session_state.game_setup.update(
                    {
                        "player_count": player_count,
                        "player_names": [name.strip() for name in player_names],
                        "draft_candidate_count_option": draft_candidate_count_option,
                        "selected_nations": selected_nations,
                        "selected_executives": selected_executives,
                        "board": board_type,
                    }
                )
                st.session_state.screen = "setup"
                st.rerun()


def show_setup_screen(contract_df, nation_df, exec_df):
    st.title("セットアップ")
    setup_data = st.session_state.game_setup
    if not setup_data["draft_order"]:
        draft_order = setup_data["player_names"].copy()
        random.shuffle(draft_order)
        setup_data["draft_order"] = draft_order
    st.header("ドラフト順")
    for i, name in enumerate(setup_data["draft_order"]):
        st.write(f"**{i+1}番手:** {name}")
    if not setup_data["nation_exec_candidates"]:
        nation_pool = setup_data["selected_nations"].copy()
        exec_pool = setup_data["selected_executives"].copy()

        count_map = {"人数と同じ": 0, "人数+1": 1, "人数+2": 2}
        num_candidates = (
            setup_data["player_count"]
            + count_map[setup_data["draft_candidate_count_option"]]
        )

        if len(nation_pool) < num_candidates or len(exec_pool) < num_candidates:
            st.error("選択された国家または重役の数が、必要な候補数より少ないです。")
            if st.button("初期画面に戻る"):
                st.session_state.screen = "setup_form"
                st.rerun()
            return

        # 直近の出現数を取得して重み付け
        nation_counts, exec_counts = get_recent_usage_counts(10)

        def get_weighted_sample(items, counts, n):
            if not items:
                return []
            df_pool = pd.DataFrame({"Name": items})
            # 重み = 1 / (出現回数 + 1)
            # 出現回数0 -> 1.0, 1 -> 0.5, 2 -> 0.33...
            df_pool["Weight"] = df_pool["Name"].apply(
                lambda x: 1.0 / (counts.get(x, 0) + 1)
            )

            # 重みに基づいてサンプリング (非復元抽出)
            sampled = df_pool.sample(n=n, weights="Weight", replace=False)
            return sampled["Name"].tolist()

        selected_nations = get_weighted_sample(
            nation_pool, nation_counts, num_candidates
        )
        selected_execs = get_weighted_sample(exec_pool, exec_counts, num_candidates)

        # ペアリング（それぞれ重み付け抽選されたリストを結合）
        candidates = list(zip(selected_nations, selected_execs))
        setup_data["nation_exec_candidates"] = candidates
        num_contracts = setup_data["player_count"]
        setup_data["contract_candidates"] = contract_df.sample(n=num_contracts).to_dict(
            "records"
        )
    st.header("国家・重役 候補")
    candidates = setup_data["nation_exec_candidates"]
    num_cols = min(len(candidates), 4)
    cols = st.columns(num_cols)
    for i, (nation_name, exec_name) in enumerate(candidates):
        with cols[i % num_cols]:
            with st.container(border=True):
                nation_icon_url = get_icon_data_url(nation_df, nation_name)
                if nation_icon_url:
                    st.image(nation_icon_url, width=50)
                st.write(f"**国家:** {nation_name}")
                st.markdown("---")
                exec_icon_url = get_icon_data_url(exec_df, exec_name)
                if exec_icon_url:
                    st.image(exec_icon_url, width=200)
                st.write(f"**重役:** {exec_name}")
    st.header("初期契約 候補")
    contract_candidates = setup_data["contract_candidates"]
    num_cols = min(len(contract_candidates), 4)
    cols = st.columns(num_cols)
    for i, contract in enumerate(contract_candidates):
        with cols[i % num_cols]:
            with st.container(border=True):
                image_url = contract.get("ImageURL")
                if image_url:
                    full_path = os.path.join(IMAGE_DIR, image_url)
                    if os.path.exists(full_path):
                        st.image(image_to_data_url(full_path))
                st.write(f"**{contract.get('Name', 'N/A')}**")
    st.header("ドラフト方式を選択")
    cols = st.columns(2)
    if cols[0].button("通常ドラフト", use_container_width=True):
        setup_data["draft_method"] = "normal"
        st.session_state.screen = "draft"
        st.rerun()
    if cols[1].button("BGAオークション方式", use_container_width=True):
        setup_data["draft_method"] = "auction"
        st.session_state.screen = "auction"
        st.rerun()


def display_draft_tile(
    column,
    item_data,
    is_selected,
    on_click,
    key,
    image_width=None,
    sub_image_width=None,
):
    with column, st.container(border=True):
        if item_data.get("image_url"):
            full_path = os.path.join(IMAGE_DIR, item_data["image_url"])
            if os.path.exists(full_path):
                if image_width:
                    st.image(image_to_data_url(full_path), width=image_width)
                else:
                    st.image(image_to_data_url(full_path))
        st.markdown(f"**{item_data['name']}**")
        if item_data.get("description"):
            st.caption(item_data["description"])
        if item_data.get("sub_name"):
            st.markdown("---")
            if item_data.get("sub_image_url"):
                full_path = os.path.join(IMAGE_DIR, item_data["sub_image_url"])
                if os.path.exists(full_path):
                    if sub_image_width:
                        st.image(image_to_data_url(full_path), width=sub_image_width)
                    else:
                        st.image(image_to_data_url(full_path))
            st.write(item_data["sub_name"])
            if item_data.get("sub_description"):
                st.caption(item_data["sub_description"])
        button_label = "解除" if is_selected else "選択"
        button_type = "primary" if is_selected else "secondary"
        if st.button(button_label, key=key, use_container_width=True, type=button_type):
            on_click()


# --- ▼▼▼ ここから変更 ▼▼▼ ---
def show_draft_screen(nation_df, exec_df):
    setup_data = st.session_state.game_setup
    if setup_data["draft_turn_index"] >= setup_data["player_count"]:
        st.session_state.screen = "draft_result"
        st.rerun()
    player_name = setup_data["draft_order"][
        st.session_state.game_setup["draft_turn_index"]
    ]
    st.title(f"ドラフト: {player_name}さんの番です")

    # --- ドラフト順の表示と現在のプレイヤーのハイライト ---
    st.header("ドラフト順")
    cols = st.columns(len(setup_data["draft_order"]))
    for i, name in enumerate(setup_data["draft_order"]):
        with cols[i]:
            if name == player_name:
                st.markdown(
                    f"<div style='padding: 10px; border: 2px solid #00ccff; border-radius: 5px; text-align: center; background-color: #e0f7fa;'><b>➡️ {name}</b></div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<div style='padding: 10px; border: 1px solid #cccccc; border-radius: 5px; text-align: center;'>{name}</div>",
                    unsafe_allow_html=True,
                )
    st.markdown("---")

    # --- 選択状況の表示 ---
    with st.container(border=True):
        st.subheader("あなたの選択")
        sel_col1, sel_col2 = st.columns(2)
        with sel_col1:
            st.markdown("##### 国家・重役")
            if setup_data["current_selection_ne"]:
                nation, exec_name = setup_data["current_selection_ne"]
                st.success(f"**選択中:** {nation} / {exec_name}")
            else:
                st.info("未選択")
        with sel_col2:
            st.markdown("##### 初期契約")
            if setup_data["current_selection_contract"]:
                st.success(
                    f"**選択中:** {setup_data['current_selection_contract']['Name']}"
                )
            else:
                st.info("未選択")

    st.divider()
    st.header("選択肢")

    # --- 国家・重役の選択肢 ---
    st.subheader("国家・重役")
    ne_candidates = setup_data["nation_exec_candidates"]
    if ne_candidates:
        num_cols = min(len(ne_candidates), 4)
        cols = st.columns(num_cols)
        for i, (nation_name, exec_name) in enumerate(ne_candidates):
            nation_row_df = nation_df[nation_df["Name"] == nation_name]
            exec_row_df = exec_df[exec_df["Name"] == exec_name]

            if nation_row_df.empty or exec_row_df.empty:
                st.error(
                    f"エラー: {nation_name} または {exec_name} のマスターデータが見つかりません。"
                )
                continue

            nation_row = nation_row_df.iloc[0]
            exec_row = exec_row_df.iloc[0]

            item_data = {
                "name": nation_name,
                "description": nation_row.get("Description"),
                "image_url": nation_row.get("IconURL"),
                "sub_name": exec_name,
                "sub_description": exec_row.get("Description"),
                "sub_image_url": exec_row.get("IconURL"),
            }
            is_selected = (nation_name, exec_name) == setup_data["current_selection_ne"]

            def on_click_ne(sel=(nation_name, exec_name), is_sel=is_selected):
                st.session_state.game_setup["current_selection_ne"] = (
                    None if is_sel else sel
                )
                st.rerun()

            display_draft_tile(
                cols[i % num_cols],
                item_data,
                is_selected,
                on_click_ne,
                f"ne_{i}",
                image_width=50,
                sub_image_width=200,
            )

    st.divider()

    # --- 初期契約の選択肢 ---
    st.subheader("初期契約")
    contract_candidates = setup_data["contract_candidates"]
    if contract_candidates:
        num_cols = min(len(contract_candidates), 4)
        cols = st.columns(num_cols)
        for i, candidate in enumerate(contract_candidates):
            item_data = {
                "name": candidate["Name"],
                "description": candidate.get("Description"),
                "image_url": candidate.get("ImageURL"),
            }
            is_selected = (
                setup_data["current_selection_contract"] is not None
                and candidate["ID"] == setup_data["current_selection_contract"]["ID"]
            )

            def on_click_contract(sel=candidate, is_sel=is_selected):
                st.session_state.game_setup["current_selection_contract"] = (
                    None if is_sel else sel
                )
                st.rerun()

            display_draft_tile(
                cols[i % num_cols],
                item_data,
                is_selected,
                on_click_contract,
                f"contract_{i}",
            )

    st.divider()

    # --- 画面下部に決定ボタンを配置 ---
    both_selected = (
        setup_data["current_selection_ne"] is not None
        and setup_data["current_selection_contract"] is not None
    )
    if st.button(
        "選択を決定する",
        type="primary",
        disabled=not both_selected,
        use_container_width=True,
        key="confirm_draft_selection",
    ):
        selected_ne = setup_data["current_selection_ne"]
        selected_contract = setup_data["current_selection_contract"]
        setup_data["draft_results"][player_name] = {
            "nation": selected_ne[0],
            "executive": selected_ne[1],
            "contract": selected_contract["Name"],
        }
        picked_nation, picked_executive = selected_ne
        setup_data["nation_exec_candidates"] = [
            (n, e)
            for n, e in setup_data["nation_exec_candidates"]
            if n != picked_nation and e != picked_executive
        ]
        setup_data["contract_candidates"] = [
            c
            for c in setup_data["contract_candidates"]
            if c["ID"] != selected_contract["ID"]
        ]
        setup_data["current_selection_ne"] = None
        setup_data["current_selection_contract"] = None
        st.session_state.game_setup["draft_turn_index"] += 1
        st.rerun()


# --- ▲▲▲ ここまで変更 ▲▲▲ ---


def get_icon_data_url(df, name, column_name="IconURL"):
    if column_name not in df.columns:
        return ""
    row = df[df["Name"] == name]
    if not row.empty:
        filename = row[column_name].iloc[0]
        if filename:
            full_path = os.path.join(IMAGE_DIR, filename)
            if os.path.exists(full_path):
                return image_to_data_url(full_path)
    return ""


def show_draft_result_screen(nation_df, exec_df):
    st.title("ドラフト結果")
    setup_data = st.session_state.game_setup
    draft_order = setup_data["draft_order"]
    draft_results = setup_data["draft_results"]
    first_round_order = list(reversed(draft_order))
    player_data_list = []
    for player_name in draft_order:
        player_result = draft_results.get(player_name, {})
        nation_name = player_result.get("nation", "N/A")
        exec_name = player_result.get("executive", "N/A")
        player_data_list.append(
            {
                "1R手番": first_round_order.index(player_name) + 1,
                "プレイヤー名": player_name,
                "国家": nation_name,
                "重役": exec_name,
                "初期契約": player_result.get("contract", "N/A"),
                "国家アイコン": get_icon_data_url(nation_df, nation_name),
                "重役アイコン": get_icon_data_url(exec_df, exec_name),
            }
        )
    player_data_list.sort(key=lambda x: x["1R手番"])
    st.subheader("ドラフト結果一覧")
    for player_data in player_data_list:
        with st.container(border=True):
            st.markdown(
                f"### {player_data['プレイヤー名']} ({player_data['1R手番']}番手)"
            )
            col1, col2 = st.columns([0.4, 0.6])
            with col1:
                if player_data["国家アイコン"]:
                    st.image(player_data["国家アイコン"], width=50)
                st.write(f"**国家:** {player_data['国家']}")
            with col2:
                if player_data["重役アイコン"]:
                    st.image(player_data["重役アイコン"], width=200)
                st.write(f"**重役:** {player_data['重役']}")
            st.markdown("---")
            st.write(f"**初期契約:** {player_data['初期契約']}")

    if st.button("ゲーム開始 (結果を保存)", type="primary", use_container_width=True):
        game_id = save_draft_to_sheet(
            setup_data["player_count"],
            setup_data["draft_order"],
            setup_data["draft_results"],
            first_round_order,
            setup_data["draft_method"],
            setup_data["board"],
        )
        if game_id:
            st.success("ドラフト結果を保存しました！")
            st.balloons()
            reset_game_setup()
            st.session_state.screen = "landing"
            st.session_state.active_game = load_latest_game_from_sheet()
            st.rerun()


def show_auction_screen(nation_df, exec_df):
    """BGAオークション方式 (グリッドUI・新ロジック・UI改善版)"""
    setup_data = st.session_state.game_setup

    # --- Phase 1: Bidding ---
    if setup_data.get("auction_phase") != "drafting":
        player_count = setup_data["player_count"]
        players = setup_data["draft_order"]

        if not setup_data.get("auction_board"):
            setup_data["auction_board"] = {}
            setup_data["auction_player_status"] = {
                p: {"status": "bidding", "turn_order": None, "bid": None}
                for p in players
            }
            setup_data["auction_log"] = ["オークションを開始します。"]

        turn_index = setup_data.get("draft_turn_index", 0)
        current_player = players[turn_index]

        st.header("選択順")
        cols = st.columns(player_count)
        for i, player_name in enumerate(players):
            with cols[i]:
                if player_name == current_player:
                    st.markdown(
                        f"<div style='padding: 10px; border: 2px solid #00ccff; border-radius: 5px; text-align: center; background-color: #e0f7fa;'><b>➡️ {player_name}</b></div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"<div style='padding: 10px; border: 1px solid #cccccc; border-radius: 5px; text-align: center;'>{player_name}</div>",
                        unsafe_allow_html=True,
                    )

        st.header(f"ターン: {current_player}さん")

        player_current_status = setup_data["auction_player_status"].get(
            current_player, {}
        )
        if player_current_status.get("status") == "displaced":
            st.warning(
                "あなたは他のプレイヤーに入札を上回られました。再度入札してください。"
            )

        # --- 新ロジック: ターン開始時のチェック ---
        player_locations = {
            v["player"]: k for k, v in setup_data["auction_board"].items()
        }
        current_player_order = player_locations.get(current_player)
        should_skip_turn = False

        if current_player_order:
            all_placed_orders = list(setup_data["auction_board"].keys())
            if all_placed_orders and max(all_placed_orders) == current_player_order:
                should_skip_turn = True
                st.success(
                    "あなたの入札が現在最高位のため、このターンはスキップされます。"
                )
                if st.button(
                    "OK、次のプレイヤーへ", key="skip_turn", use_container_width=True
                ):
                    setup_data["draft_turn_index"] = (turn_index + 1) % player_count
                    st.rerun()

        st.divider()

        if not should_skip_turn:
            st.subheader("国家・重役 候補")
            candidates = setup_data.get("nation_exec_candidates", [])
            num_cols = min(len(candidates), 5)
            if candidates:
                cols = st.columns(num_cols)
                for i, (nation_name, exec_name) in enumerate(candidates):
                    with cols[i % num_cols]:
                        with st.container(border=True):
                            nation_icon_url = get_icon_data_url(nation_df, nation_name)
                            if nation_icon_url:
                                st.image(nation_icon_url, width=50)
                            st.write(f"**{nation_name}**")
                            st.markdown("---")
                            exec_icon_url = get_icon_data_url(exec_df, exec_name)
                            if exec_icon_url:
                                st.image(exec_icon_url, width=200)
                            st.write(f"**{exec_name}**")

            st.divider()
            st.header("入札ボード")

            player_locations_for_grid = {
                v["player"]: {"turn_order": k, "bid": v["bid"]}
                for k, v in setup_data["auction_board"].items()
            }

            vp_cols = st.columns(17)
            vp_cols[0].write("**手番**")
            for vp in range(16):
                vp_cols[vp + 1].write(f"**{vp}**")

            for turn_order in range(1, player_count + 1):
                row_cols = st.columns(17)
                row_cols[0].write(f"**{turn_order}番手**")
                current_bid_on_spot = setup_data["auction_board"].get(turn_order)

                for bid_vp in range(16):
                    cell_key = f"cell_{turn_order}_{bid_vp}"
                    is_occupied = False
                    occupying_player = ""

                    if current_bid_on_spot and current_bid_on_spot["bid"] == bid_vp:
                        is_occupied = True
                        occupying_player = current_bid_on_spot["player"]

                    button_label = occupying_player if is_occupied else " "

                    if row_cols[bid_vp + 1].button(
                        button_label, key=cell_key, use_container_width=True
                    ):
                        is_valid_bid = True
                        if is_occupied and occupying_player != current_player:
                            st.warning("この場所は他のプレイヤーに確保されています。")
                            is_valid_bid = False
                        if current_bid_on_spot and bid_vp < current_bid_on_spot["bid"]:
                            st.warning(
                                f"この手番には既により高い入札({current_bid_on_spot['bid']}VP)があります。"
                            )
                            is_valid_bid = False

                        if is_valid_bid:
                            if (
                                current_bid_on_spot
                                and current_bid_on_spot["player"] != current_player
                            ):
                                displaced_player = current_bid_on_spot["player"]
                                setup_data["auction_player_status"][
                                    displaced_player
                                ] = {
                                    "status": "displaced",
                                    "turn_order": None,
                                    "bid": None,
                                }
                                log_message = f"-> {current_player}が{displaced_player}の入札を上回りました！ {displaced_player}は再度入札が必要です。"
                                setup_data["auction_log"].insert(0, log_message)

                            if current_player in player_locations_for_grid:
                                old_location = player_locations_for_grid[current_player]
                                old_turn_order = old_location["turn_order"]
                                if (
                                    old_turn_order in setup_data["auction_board"]
                                    and setup_data["auction_board"][old_turn_order][
                                        "player"
                                    ]
                                    == current_player
                                ):
                                    del setup_data["auction_board"][old_turn_order]

                            log_message = f'-> {current_player}が"{turn_order}番手"に"{bid_vp}VP"で入札しました。'
                            setup_data["auction_log"].insert(0, log_message)

                            setup_data["auction_board"][turn_order] = {
                                "player": current_player,
                                "bid": bid_vp,
                            }
                            setup_data["auction_player_status"][current_player] = {
                                "status": "placed",
                                "turn_order": turn_order,
                                "bid": bid_vp,
                            }

                            check_and_handle_auction_end(setup_data)
                            setup_data["draft_turn_index"] = (
                                turn_index + 1
                            ) % player_count
                            st.rerun()

        st.divider()
        st.subheader("ログ")
        with st.container(height=200):
            for log_entry in setup_data["auction_log"]:
                st.text(log_entry)

        if st.button("セットアップに戻る"):
            st.session_state.screen = "setup"
            st.rerun()

    # --- Phase 2: Drafting (remains the same) ---
    else:
        st.header("オークション結果")
        final_order_df = pd.DataFrame(
            [
                {
                    "手番": order_num,
                    "プレイヤー": setup_data["auction_board"][order_num]["player"],
                    "入札額": setup_data["auction_board"][order_num]["bid"],
                }
                for order_num in sorted(setup_data["auction_board"].keys())
            ]
        )
        st.dataframe(final_order_df, hide_index=True, use_container_width=True)

        st.header("ドラフト")
        draft_order = setup_data["auction_draft_order"]
        draft_turn_index = setup_data.get("draft_turn_index", 0)

        if draft_turn_index >= len(draft_order):
            st.success("全員のドラフトが完了しました！")
            if st.button(
                "ゲーム開始（結果を保存）", type="primary", use_container_width=True
            ):
                final_turn_order = setup_data["final_turn_order"]
                for p_name, p_status in setup_data["auction_player_status"].items():
                    if p_name not in setup_data["draft_results"]:
                        setup_data["draft_results"][p_name] = {}
                    setup_data["draft_results"][p_name]["bid"] = p_status["bid"]

                game_id = save_draft_to_sheet(
                    setup_data["player_count"],
                    draft_order,
                    setup_data["draft_results"],
                    final_turn_order,
                    setup_data["draft_method"],
                    setup_data["board"],
                )
                if game_id:
                    st.success("ドラフト結果を保存しました！")
                    st.balloons()
                    reset_game_setup()
                    st.session_state.screen = "landing"
                    st.session_state.active_game = load_latest_game_from_sheet()
                    st.rerun()
        else:
            draft_player = draft_order[draft_turn_index]
            st.subheader(f"ドラフト: {draft_player}さんの番です")

            with st.container(border=True):
                st.subheader("あなたの選択")
                sel_col1, sel_col2 = st.columns(2)
                with sel_col1:
                    st.markdown("##### 国家・重役")
                    if setup_data.get("current_selection_ne"):
                        nation, exec_name = setup_data["current_selection_ne"]
                        st.success(f"**選択中:** {nation} / {exec_name}")
                    else:
                        st.info("未選択")
                with sel_col2:
                    st.markdown("##### 初期契約")
                    if setup_data.get("current_selection_contract"):
                        st.success(
                            f"**選択中:** {setup_data['current_selection_contract']['Name']}"
                        )
                    else:
                        st.info("未選択")
                st.markdown("---")
                both_selected = (
                    setup_data.get("current_selection_ne") is not None
                    and setup_data.get("current_selection_contract") is not None
                )
                if st.button(
                    "選択を決定する",
                    type="primary",
                    disabled=not both_selected,
                    use_container_width=True,
                ):
                    selected_ne = setup_data["current_selection_ne"]
                    selected_contract = setup_data["current_selection_contract"]
                    setup_data["draft_results"][draft_player] = {
                        "nation": selected_ne[0],
                        "executive": selected_ne[1],
                        "contract": selected_contract["Name"],
                    }
                    picked_nation, picked_executive = selected_ne
                    setup_data["nation_exec_candidates"] = [
                        (n, e)
                        for n, e in setup_data["nation_exec_candidates"]
                        if n != picked_nation and e != picked_executive
                    ]
                    setup_data["contract_candidates"] = [
                        c
                        for c in setup_data["contract_candidates"]
                        if c["ID"] != selected_contract["ID"]
                    ]
                    setup_data["current_selection_ne"] = None
                    setup_data["current_selection_contract"] = None
                    setup_data["draft_turn_index"] += 1
                    st.rerun()

            st.divider()
            st.header("選択肢")
            st.subheader("国家・重役")
            ne_candidates = setup_data["nation_exec_candidates"]
            if ne_candidates:
                num_cols = min(len(ne_candidates), 4)
                cols = st.columns(num_cols)
                for i, (nation_name, exec_name) in enumerate(ne_candidates):
                    nation_row_df = nation_df[nation_df["Name"] == nation_name]
                    exec_row_df = exec_df[exec_df["Name"] == exec_name]

                    if nation_row_df.empty or exec_row_df.empty:
                        st.error(
                            f"エラー: {nation_name} または {exec_name} のマスターデータが見つかりません。"
                        )
                        continue

                    nation_row = nation_row_df.iloc[0]
                    exec_row = exec_row_df.iloc[0]

                    item_data = {
                        "name": nation_name,
                        "description": nation_row.get("Description"),
                        "image_url": nation_row.get("IconURL"),
                        "sub_name": exec_name,
                        "sub_description": exec_row.get("Description"),
                        "sub_image_url": exec_row.get("IconURL"),
                    }
                    is_selected = (nation_name, exec_name) == setup_data.get(
                        "current_selection_ne"
                    )

                    def on_click_ne(sel=(nation_name, exec_name), is_sel=is_selected):
                        st.session_state.game_setup["current_selection_ne"] = (
                            None if is_sel else sel
                        )
                        st.rerun()

                    display_draft_tile(
                        cols[i % num_cols],
                        item_data,
                        is_selected,
                        on_click_ne,
                        f"auction_ne_{i}",
                        image_width=50,
                        sub_image_width=200,
                    )

            st.divider()
            st.subheader("初期契約")
            contract_candidates = setup_data["contract_candidates"]
            if contract_candidates:
                num_cols = min(len(contract_candidates), 4)
                cols = st.columns(num_cols)
                for i, candidate in enumerate(contract_candidates):
                    item_data = {
                        "name": candidate["Name"],
                        "description": candidate.get("Description"),
                        "image_url": candidate.get("ImageURL"),
                    }
                    is_selected = (
                        setup_data["current_selection_contract"] is not None
                        and candidate["ID"]
                        == setup_data["current_selection_contract"]["ID"]
                    )

                    def on_click_contract(sel=candidate, is_sel=is_selected):
                        st.session_state.game_setup["current_selection_contract"] = (
                            None if is_sel else sel
                        )
                        st.rerun()

                    display_draft_tile(
                        cols[i % num_cols],
                        item_data,
                        is_selected,
                        on_click_contract,
                        f"auction_contract_{i}",
                    )


def show_score_input_screen():
    st.title("スコア入力")

    active_game_data = st.session_state.active_game
    if not active_game_data:
        st.error("スコア入力対象의 게임が見つかりません。")
        if st.button("初期画面に戻る"):
            st.session_state.screen = "landing"
            st.rerun()
        return

    game_id = active_game_data[0]["GameID"]
    players = [p["PlayerName"] for p in active_game_data]

    st.subheader(f"ゲームID: {game_id}")

    with st.form("score_form"):
        player_scores = {}
        for player in players:
            player_scores[player] = st.number_input(
                f"{player} のスコア", min_value=0, step=1, key=f"score_{player}"
            )

        submitted = st.form_submit_button("スコアを保存", type="primary")
        if submitted:
            if update_scores_in_sheet(game_id, player_scores):
                st.success("スコアを保存しました！")
                st.balloons()
                st.session_state.active_game = None
                st.session_state.screen = "landing"
                st.rerun()


# --- 統計機能 ---
@st.cache_data(ttl=60)
def load_all_scores_from_sheet():
    """スコア記録シートから全データを読み込む（FinalScoreが入力済みのもののみ）"""
    try:
        worksheet = get_score_sheet()
        all_values = worksheet.get_all_values()
        if not all_values or len(all_values) < 2:
            return None

        headers = all_values[0]
        rows = all_values[1:]
        df = pd.DataFrame(rows, columns=headers)

        # 必須カラムの確認
        required_cols = ["GameID", "PlayerName", "FinalScore", "Nation", "Executive"]
        for col in required_cols:
            if col not in df.columns:
                return None

        # FinalScoreが入力されているレコードのみ抽出
        df = df[df["FinalScore"].astype(str).str.strip() != ""]

        # 数値型に変換
        df["GameID"] = pd.to_numeric(df["GameID"], errors="coerce")
        df["FinalScore"] = pd.to_numeric(df["FinalScore"], errors="coerce")
        df["InitialScore"] = pd.to_numeric(df["InitialScore"], errors="coerce")
        df["TurnOrder1R"] = pd.to_numeric(df["TurnOrder1R"], errors="coerce")
        df["PlayerCount"] = pd.to_numeric(df["PlayerCount"], errors="coerce")

        # Timestampをdatetime型に変換
        if "Timestamp" in df.columns:
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")

        df = df.dropna(subset=["GameID", "FinalScore"])
        return df
    except Exception as e:
        st.error(f"統計データの読み込み中にエラーが発生しました: {e}")
        return None


def filter_df_by_period(df, period_option, start_date=None, end_date=None):
    """期間でDataFrameをフィルタリングする"""
    if df is None or df.empty or "Timestamp" not in df.columns:
        return df

    now = datetime.now()
    cutoff_start = None
    cutoff_end = None

    if period_option == "直近30日":
        cutoff_start = now - timedelta(days=30)
    elif period_option == "直近90日":
        cutoff_start = now - timedelta(days=90)
    elif period_option == "直近1年":
        cutoff_start = now - timedelta(days=365)
    elif period_option == "日付指定":
        if start_date:
            cutoff_start = datetime.combine(start_date, datetime.min.time())
        if end_date:
            cutoff_end = datetime.combine(end_date, datetime.max.time())
    else:  # 全期間
        return df

    if cutoff_start:
        df = df[df["Timestamp"] >= cutoff_start]
    if cutoff_end:
        df = df[df["Timestamp"] <= cutoff_end]

    return df


def calculate_player_stats(df):
    """プレイヤー別統計を計算"""
    if df is None or df.empty:
        return None

    # ゲームごとの順位を計算
    df = df.copy()
    df["Rank"] = df.groupby("GameID")["FinalScore"].rank(ascending=False, method="min")

    stats = []
    for player_name in df["PlayerName"].unique():
        player_df = df[df["PlayerName"] == player_name]
        game_count = player_df["GameID"].nunique()
        win_count = len(player_df[player_df["Rank"] == 1])

        stats.append({
            "プレイヤー": player_name,
            "ゲーム数": game_count,
            "勝利数": win_count,
            "勝率": f"{(win_count / game_count * 100):.1f}%" if game_count > 0 else "0%",
            "平均スコア": round(player_df["FinalScore"].mean(), 1),
            "最高スコア": int(player_df["FinalScore"].max()),
            "平均順位": round(player_df["Rank"].mean(), 2),
        })

    return pd.DataFrame(stats).sort_values("勝率", ascending=False)


def calculate_nation_stats(df):
    """国家別統計を計算"""
    if df is None or df.empty:
        return None

    df = df.copy()
    df["Rank"] = df.groupby("GameID")["FinalScore"].rank(ascending=False, method="min")

    stats = []
    for nation in df["Nation"].unique():
        nation_df = df[df["Nation"] == nation]
        use_count = len(nation_df)
        win_count = len(nation_df[nation_df["Rank"] == 1])

        stats.append({
            "国家": nation,
            "使用回数": use_count,
            "勝利数": win_count,
            "勝率": f"{(win_count / use_count * 100):.1f}%" if use_count > 0 else "0%",
            "平均スコア": round(nation_df["FinalScore"].mean(), 1),
        })

    return pd.DataFrame(stats).sort_values("使用回数", ascending=False)


def calculate_executive_stats(df):
    """重役別統計を計算"""
    if df is None or df.empty:
        return None

    df = df.copy()
    df["Rank"] = df.groupby("GameID")["FinalScore"].rank(ascending=False, method="min")

    stats = []
    for exec_name in df["Executive"].unique():
        exec_df = df[df["Executive"] == exec_name]
        use_count = len(exec_df)
        win_count = len(exec_df[exec_df["Rank"] == 1])

        stats.append({
            "重役": exec_name,
            "使用回数": use_count,
            "勝利数": win_count,
            "勝率": f"{(win_count / use_count * 100):.1f}%" if use_count > 0 else "0%",
            "平均スコア": round(exec_df["FinalScore"].mean(), 1),
        })

    return pd.DataFrame(stats).sort_values("使用回数", ascending=False)


def calculate_combination_stats(df):
    """国家・重役の組み合わせ別統計を計算"""
    if df is None or df.empty:
        return None

    df = df.copy()
    df["Rank"] = df.groupby("GameID")["FinalScore"].rank(ascending=False, method="min")
    df["組み合わせ"] = df["Nation"] + " × " + df["Executive"]

    stats = []
    for combo in df["組み合わせ"].unique():
        combo_df = df[df["組み合わせ"] == combo]
        use_count = len(combo_df)
        win_count = len(combo_df[combo_df["Rank"] == 1])

        # 国家と重役を分離
        nation = combo_df["Nation"].iloc[0]
        executive = combo_df["Executive"].iloc[0]

        stats.append({
            "国家": nation,
            "重役": executive,
            "組み合わせ": combo,
            "使用回数": use_count,
            "勝利数": win_count,
            "勝率": f"{(win_count / use_count * 100):.1f}%" if use_count > 0 else "0%",
            "勝率数値": (win_count / use_count * 100) if use_count > 0 else 0,
            "平均スコア": round(combo_df["FinalScore"].mean(), 1),
        })

    return pd.DataFrame(stats).sort_values("使用回数", ascending=False)


def show_stats_screen():
    """統計画面を表示"""
    import altair as alt

    st.title("📊 統計")

    # 戻るボタン
    if st.button("← 戻る"):
        st.session_state.screen = "landing"
        st.rerun()

    # データ読み込み
    df = load_all_scores_from_sheet()

    if df is None or df.empty:
        st.warning("統計データがありません。ゲームを完了してスコアを入力してください。")
        return

    # 期間フィルター
    st.sidebar.header("フィルター")

    # バランス調整バージョンでのフィルタ
    balance_log = get_balance_log()
    # 日付降順にソート
    try:
        balance_log.sort(key=lambda x: x["Date"], reverse=True)
    except:
        pass

    version_options = ["指定なし"] + [
        f"{r['Date']} : {r['Version']}" for r in balance_log if r.get("Date")
    ]
    selected_version = st.sidebar.selectbox("バランス調整バージョン", version_options)

    start_date = None
    end_date = None
    selected_period = "全期間"  # デフォルト

    if selected_version != "指定なし":
        # バージョン選択時はその日付を開始日とする
        date_str = selected_version.split(" : ")[0]
        try:
            start_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            selected_period = "日付指定"
            st.sidebar.info(f"{date_str} 以降のデータを表示中")
        except ValueError:
            pass
    else:
        # 通常の期間選択
        period_options = ["全期間", "直近30日", "直近90日", "直近1年", "日付指定"]
        selected_period = st.sidebar.selectbox("期間", period_options)

        if selected_period == "日付指定":
            col_d1, col_d2 = st.sidebar.columns(2)
            with col_d1:
                start_date = st.date_input(
                    "開始日", value=datetime.now() - timedelta(days=30)
                )
            with col_d2:
                end_date = st.date_input("終了日", value=datetime.now())

    df = filter_df_by_period(df, selected_period, start_date, end_date)

    if df.empty:
        st.warning(f"選択した期間（{selected_period}）にはデータがありません。")
        return

    # ボードフィルター
    st.sidebar.divider()
    available_boards = ["すべて"] + sorted(df["Board"].dropna().unique().tolist())
    selected_board = st.sidebar.selectbox("ボード", available_boards)
    if selected_board != "すべて":
        df = df[df["Board"] == selected_board]

    # プレイヤー数フィルター
    available_counts = ["すべて"] + sorted([int(x) for x in df["PlayerCount"].dropna().unique()])
    selected_count = st.sidebar.selectbox("プレイヤー数", available_counts)
    if selected_count != "すべて":
        df = df[df["PlayerCount"] == selected_count]

    # ドラフト方式フィルター
    draft_method_map = {"すべて": None, "通常ドラフト": "normal", "オークション": "auction"}
    selected_method_display = st.sidebar.selectbox("ドラフト方式", list(draft_method_map.keys()))
    selected_method = draft_method_map[selected_method_display]
    if selected_method is not None:
        df = df[df["DraftMethod"] == selected_method]

    if df.empty:
        st.warning("選択した条件に一致するデータがありません。")
        return

    # タブで統計カテゴリを分ける
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📈 総合", "👤 プレイヤー", "🏛️ 国家", "👔 重役", "🔗 組み合わせ"])

    with tab1:
        st.header("総合統計")

        # メトリクスカード
        col1, col2, col3, col4 = st.columns(4)
        total_games = df["GameID"].nunique()
        total_players = df["PlayerName"].nunique()
        avg_score = df["FinalScore"].mean()
        max_score = df["FinalScore"].max()

        col1.metric("総ゲーム数", total_games)
        col2.metric("参加プレイヤー数", total_players)
        col3.metric("平均スコア", f"{avg_score:.1f}")
        col4.metric("最高スコア", int(max_score))

        st.divider()

        # スコア分布グラフ
        st.subheader("スコア分布")
        score_hist = alt.Chart(df).mark_bar().encode(
            alt.X("FinalScore:Q", bin=alt.Bin(maxbins=20), title="スコア"),
            alt.Y("count()", title="回数"),
            tooltip=["count()"]
        ).properties(height=300)
        st.altair_chart(score_hist, use_container_width=True)

        # 時系列グラフ（ゲームごとの平均スコア推移）
        if "Timestamp" in df.columns:
            st.subheader("平均スコア推移")
            game_avg = df.groupby(["GameID", "Timestamp"]).agg({
                "FinalScore": "mean"
            }).reset_index().sort_values("Timestamp")

            line_chart = alt.Chart(game_avg).mark_line(point=True).encode(
                alt.X("Timestamp:T", title="日時"),
                alt.Y("FinalScore:Q", title="平均スコア"),
                tooltip=["Timestamp:T", alt.Tooltip("FinalScore:Q", format=".1f")]
            ).properties(height=300)
            st.altair_chart(line_chart, use_container_width=True)

    with tab2:
        st.header("プレイヤー別統計")
        player_stats = calculate_player_stats(df)
        if player_stats is not None and not player_stats.empty:
            st.dataframe(player_stats, use_container_width=True, hide_index=True)

            # プレイヤー別平均スコアグラフ
            st.subheader("プレイヤー別平均スコア")
            player_chart = alt.Chart(player_stats).mark_bar().encode(
                alt.X("プレイヤー:N", sort="-y", title="プレイヤー"),
                alt.Y("平均スコア:Q", title="平均スコア"),
                color=alt.Color("勝率:N", legend=None),
                tooltip=["プレイヤー", "平均スコア", "勝率", "ゲーム数"]
            ).properties(height=300)
            st.altair_chart(player_chart, use_container_width=True)
        else:
            st.info("データがありません。")

    with tab3:
        st.header("国家別統計")
        nation_stats = calculate_nation_stats(df)
        if nation_stats is not None and not nation_stats.empty:
            st.dataframe(nation_stats, use_container_width=True, hide_index=True)

            # 国家別使用回数グラフ
            st.subheader("国家別使用回数")
            nation_chart = alt.Chart(nation_stats).mark_bar().encode(
                alt.X("国家:N", sort="-y", title="国家"),
                alt.Y("使用回数:Q", title="使用回数"),
                color=alt.value("#4CAF50"),
                tooltip=["国家", "使用回数", "勝率", "平均スコア"]
            ).properties(height=300)
            st.altair_chart(nation_chart, use_container_width=True)
        else:
            st.info("データがありません。")

    with tab4:
        st.header("重役別統計")
        exec_stats = calculate_executive_stats(df)
        if exec_stats is not None and not exec_stats.empty:
            st.dataframe(exec_stats, use_container_width=True, hide_index=True)

            # 重役別使用回数グラフ
            st.subheader("重役別使用回数")
            exec_chart = alt.Chart(exec_stats).mark_bar().encode(
                alt.X("重役:N", sort="-y", title="重役"),
                alt.Y("使用回数:Q", title="使用回数"),
                color=alt.value("#2196F3"),
                tooltip=["重役", "使用回数", "勝率", "平均スコア"]
            ).properties(height=300)
            st.altair_chart(exec_chart, use_container_width=True)
        else:
            st.info("データがありません。")

    with tab5:
        st.header("国家×重役 組み合わせ統計")
        combo_stats = calculate_combination_stats(df)
        if combo_stats is not None and not combo_stats.empty:
            # 表示用に列を選択
            display_cols = ["国家", "重役", "使用回数", "勝利数", "勝率", "平均スコア"]
            st.dataframe(combo_stats[display_cols], use_container_width=True, hide_index=True)

            # 組み合わせ別平均スコアグラフ（上位10件）
            st.subheader("組み合わせ別平均スコア（使用回数上位10件）")
            top_combos = combo_stats.head(10)
            combo_chart = alt.Chart(top_combos).mark_bar().encode(
                alt.Y("組み合わせ:N", sort="-x", title="組み合わせ", axis=alt.Axis(labelLimit=300)),
                alt.X("平均スコア:Q", title="平均スコア"),
                color=alt.Color("勝率数値:Q", scale=alt.Scale(scheme="viridis"), title="勝率(%)"),
                tooltip=["国家", "重役", "使用回数", "勝率", "平均スコア"]
            ).properties(height=400)
            st.altair_chart(combo_chart, use_container_width=True)

            # ヒートマップ（国家×重役の平均スコア）
            st.subheader("国家×重役 平均スコア ヒートマップ")
            heatmap = alt.Chart(combo_stats).mark_rect().encode(
                alt.X("国家:N", title="国家"),
                alt.Y("重役:N", title="重役", axis=alt.Axis(labelLimit=200)),
                alt.Color("平均スコア:Q", scale=alt.Scale(scheme="blues"), title="平均スコア"),
                tooltip=["国家", "重役", "使用回数", "勝率", "平均スコア"]
            ).properties(height=600, width=600)
            st.altair_chart(heatmap, use_container_width=True)
        else:
            st.info("データがありません。")


def show_master_editor_screen():
    """マスタデータ編集画面"""
    st.title("🔧 マスタデータ編集")

    if st.button("← 戻る"):
        st.session_state.screen = "landing"
        st.rerun()

    tab1, tab2 = st.tabs(["🏛️ 国家", "👔 重役"])

    # 共通の編集ロジック
    def render_editor(sheet_name, entity_label):
        df = get_master_data(sheet_name)
        if df is None or df.empty:
            st.error("データが読み込めませんでした")
            return

        all_names = df["Name"].tolist()
        selected_name = st.selectbox(f"編集する{entity_label}", all_names)

        if selected_name:
            # 選択された名前の最新データを取得
            current_data = df[df["Name"] == selected_name].iloc[0]

            with st.form(f"edit_form_{sheet_name}"):
                st.subheader(f"{selected_name} の編集")

                # 既存データの表示と編集
                new_desc = st.text_area(
                    "説明 (Description)", value=current_data.get("Description", "")
                )
                new_icon = st.text_input(
                    "アイコン (IconURL)", value=current_data.get("IconURL", "")
                )

                st.divider()
                st.write("▼ 更新情報")
                col1, col2 = st.columns(2)
                with col1:
                    new_date = st.date_input("適用日 (EffectiveDate)", value=datetime.now())
                with col2:
                    version_name = st.text_input("バージョン名 (例: v1.1)", placeholder="必須")

                change_note = st.text_area("変更内容メモ (バランス調整履歴に追記されます)")

                if st.form_submit_button("保存（追記）"):
                    if not version_name:
                        st.error("バージョン名を入力してください")
                        return

                    # 保存用データ作成
                    save_data = current_data.to_dict()
                    save_data["Description"] = new_desc
                    save_data["IconURL"] = new_icon
                    save_data["EffectiveDate"] = str(new_date)

                    if save_master_update(sheet_name, save_data):
                        # バランス調整ログにも記録
                        log_msg = (
                            f"[{entity_label}] {selected_name}: {change_note}"
                            if change_note
                            else f"[{entity_label}] {selected_name} 更新"
                        )
                        add_balance_log(str(new_date), version_name, log_msg)

                        st.success(f"{selected_name} を更新しました！")
                        st.balloons()
                        # 少し待ってリロード
                        import time

                        time.sleep(1)
                        st.rerun()

    with tab1:
        render_editor(NATION_SHEET, "国家")
    with tab2:
        render_editor(EXECUTIVE_SHEET, "重役")


# --- メイン処理 ---
def main():
    st.set_page_config(layout="wide", page_title="バラージ セットアップランダマイザ")

    st.markdown(
        """
        <style>
            div[data-testid="stImage"] > img {
                max-width: 300px !important;
                display: block !important;
                margin-left: auto !important;
                margin-right: auto !important;
            }
            .block-container {
                max-width: 1500px;
                margin: auto;
            }
            /* Add custom CSS for the bidding board buttons */
            div[data-testid="stHorizontalBlock"] button {
                min-height: 40px;
            }
        </style>
    """,
        unsafe_allow_html=True,
    )

    initialize_session_state()

    if st.session_state.active_game is None:
        st.session_state.active_game = load_latest_game_from_sheet()

    screen = st.session_state.screen

    if screen == "landing":
        show_landing_screen()
    elif screen == "setup_form":
        nation_df = get_master_data(NATION_SHEET)
        exec_df = get_master_data(EXECUTIVE_SHEET)
        if nation_df is not None and exec_df is not None:
            show_setup_form_screen(nation_df, exec_df)
    elif screen == "setup":
        contract_df = get_master_data(CONTRACT_SHEET)
        nation_df = get_master_data(NATION_SHEET)
        exec_df = get_master_data(EXECUTIVE_SHEET)
        if contract_df is not None and nation_df is not None and exec_df is not None:
            show_setup_screen(contract_df, nation_df, exec_df)
    elif screen == "draft":
        nation_df = get_master_data(NATION_SHEET)
        exec_df = get_master_data(EXECUTIVE_SHEET)
        if nation_df is not None and exec_df is not None:
            show_draft_screen(nation_df, exec_df)
    elif screen == "draft_result":
        nation_df = get_master_data(NATION_SHEET)
        exec_df = get_master_data(EXECUTIVE_SHEET)
        if nation_df is not None and exec_df is not None:
            show_draft_result_screen(nation_df, exec_df)
    elif screen == "auction":
        nation_df = get_master_data(NATION_SHEET)
        exec_df = get_master_data(EXECUTIVE_SHEET)
        if nation_df is not None and exec_df is not None:
            show_auction_screen(nation_df, exec_df)
    elif screen == "score_input":
        show_score_input_screen()
    elif screen == "stats":
        show_stats_screen()
    elif screen == "master_editor":
        show_master_editor_screen()
    else:
        st.session_state.screen = "landing"
        st.rerun()


if __name__ == "__main__":
    main()
