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
IMAGE_DIR = "images"


# --- [追加] スプレッドシート操作 ---
@st.cache_resource(ttl=600)
def get_gspread_client():
    """gspreadクライアントを取得する（キャッシュ活用）"""
    return gspread.service_account_from_dict(st.secrets["gcp_service_account"])


def get_score_sheet():
    """スコア記録シートのワークシートオブジェクトを取得する"""
    gc = get_gspread_client()
    sh = gc.open_by_key(SPREADSHEET_KEY)
    return sh.worksheet(SCORE_SHEET)


def save_draft_to_sheet(
    player_count, draft_order, draft_results, first_round_order, draft_method
):
    """[修正] ドラフト結果をスプレッドシートに保存する（列名参照）"""
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
            ]
            worksheet.append_row(header, value_input_option="USER_ENTERED")
        else:
            header = all_values[0]

        rows_to_append = []
        for player_name in draft_order:
            result = draft_results[player_name]
            turn_order = first_round_order.index(player_name) + 1
            initial_score = 10 if draft_method == "normal" else 0

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
            }

            # ヘッダーの順番に合わせてリストを作成
            row = [data_dict.get(h, "") for h in header]
            rows_to_append.append(row)

        worksheet.append_rows(rows_to_append, value_input_option="USER_ENTERED")
        return game_id
    except Exception as e:
        st.error(f"スプレッドシートへの書き込み中にエラーが発生しました: {e}")
        return None


@st.cache_data(ttl=60)  # 1分キャッシュ
def load_latest_game_from_sheet():
    """スコアが未入力の最新のゲームデータをシートから読み込む"""
    try:
        worksheet = get_score_sheet()
        data = worksheet.get_all_records()
        if not data:
            return None

        df = pd.DataFrame(data)
        if "FinalScore" not in df.columns:
            return None

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


def update_scores_in_sheet(game_id, player_scores):
    """[修正] 指定されたGameIDのスコアを更新する（列名参照）"""
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
@st.cache_data(ttl=600)
def get_master_data(worksheet_name):
    """指定されたワークシートからデータを読み込み、DataFrameとして返す"""
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(SPREADSHEET_KEY)
        worksheet = sh.worksheet(worksheet_name)
        data = worksheet.get_all_values()
        headers = data[0]
        df_data = data[1:]
        return pd.DataFrame(df_data, columns=headers)
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
    }


# --- 画面描画関数 ---


def show_landing_screen():
    """アプリ起動時の初期画面"""
    st.title("バラージ セットアップ & スコア管理")

    # --- [修正] 更新ボタンを追加 ---
    col1, col2 = st.columns([0.7, 0.3])
    with col2:
        if st.button("最新の情報に更新", use_container_width=True):
            st.cache_data.clear()
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
            st.write(f"**ゲーム開始日時:** {game_time} ({draft_method_jp})")

            display_df = pd.DataFrame(latest_game)[
                ["PlayerName", "TurnOrder1R", "Nation", "Executive", "Contract"]
            ]
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            if st.button("スコアを入力する", type="primary", use_container_width=True):
                st.session_state.screen = "score_input"
                st.rerun()
        st.divider()

    if st.button("新規セットアップ", use_container_width=True):
        reset_game_setup()
        st.session_state.screen = "setup_form"
        st.rerun()


def show_setup_form_screen(nation_df, exec_df):
    """セットアップ情報を入力する画面"""
    st.title("新規セットアップ")

    with st.form("initial_setup_form"):
        st.header("1. ゲーム設定")
        st.subheader("使用する国家・重役")
        all_nations = nation_df["Name"].tolist()
        all_executives = exec_df["Name"].tolist()
        selected_nations = st.multiselect(
            "国家を選択", all_nations, default=all_nations
        )
        selected_executives = st.multiselect(
            "重役を選択", all_executives, default=all_executives
        )
        st.header("2. プレイヤー設定")
        cols = st.columns(2)
        with cols[0]:
            player_count = st.number_input(
                "プレイヤー数",
                min_value=1,
                max_value=5,
                value=st.session_state.game_setup["player_count"],
            )
        with cols[1]:
            draft_options = ["人数と同じ", "人数+1", "人数+2"]
            draft_candidate_count_option = st.radio(
                "ドラフト候補数",
                draft_options,
                index=draft_options.index(
                    st.session_state.game_setup["draft_candidate_count_option"]
                ),
            )
        player_names = []
        st.subheader("プレイヤー名")
        for i in range(player_count):
            player_names.append(
                st.text_input(
                    f"プレイヤー {i+1}", value=f"Player {i+1}", key=f"player_{i}"
                )
            )
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
                    }
                )
                st.session_state.screen = "setup"
                st.rerun()


def show_setup_screen(contract_df):
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
        random.shuffle(nation_pool)
        random.shuffle(exec_pool)
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
        candidates = []
        for _ in range(num_candidates):
            candidates.append((nation_pool.pop(), exec_pool.pop()))
        setup_data["nation_exec_candidates"] = candidates
        num_contracts = setup_data["player_count"]
        setup_data["contract_candidates"] = contract_df.sample(n=num_contracts).to_dict(
            "records"
        )
    st.header("国家・重役 候補")
    st.table(
        pd.DataFrame(setup_data["nation_exec_candidates"], columns=["国家", "重役"])
    )
    st.header("初期契約 候補")
    st.table(pd.DataFrame(setup_data["contract_candidates"])[["Name"]])
    st.header("ドラフト方式を選択")
    cols = st.columns(2)
    if cols[0].button("通常ドラフト", use_container_width=True):
        setup_data["draft_method"] = "normal"
        st.session_state.screen = "draft"
        st.rerun()
    if cols[1].button("BGAオークション方式", use_container_width=True):
        setup_data["draft_method"] = "auction"
        st.info("BGAオークション方式は現在開発中です。")


def display_draft_tile(column, item_data, is_selected, on_click, key):
    with column, st.container(border=True):
        if item_data.get("image_url"):
            full_path = os.path.join(IMAGE_DIR, item_data["image_url"])
            if os.path.exists(full_path):
                st.image(image_to_data_url(full_path))
        st.markdown(f"**{item_data['name']}**")
        if item_data.get("description"):
            st.caption(item_data["description"])
        if item_data.get("sub_name"):
            st.markdown("---")
            if item_data.get("sub_image_url"):
                full_path = os.path.join(IMAGE_DIR, item_data["sub_image_url"])
                if os.path.exists(full_path):
                    st.image(image_to_data_url(full_path))
            st.write(item_data["sub_name"])
            if item_data.get("sub_description"):
                st.caption(item_data["sub_description"])
        button_label = "解除" if is_selected else "選択"
        button_type = "primary" if is_selected else "secondary"
        if st.button(button_label, key=key, use_container_width=True, type=button_type):
            on_click()


def show_draft_screen(nation_df, exec_df):
    setup_data = st.session_state.game_setup
    if setup_data["draft_turn_index"] >= setup_data["player_count"]:
        st.session_state.screen = "draft_result"
        st.rerun()
    player_name = setup_data["draft_order"][setup_data["draft_turn_index"]]
    st.title(f"ドラフト: {player_name}さんの番です")
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
        st.markdown("---")
        both_selected = (
            setup_data["current_selection_ne"] is not None
            and setup_data["current_selection_contract"] is not None
        )
        if st.button(
            "選択を決定する",
            type="primary",
            disabled=not both_selected,
            use_container_width=True,
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
            nation_row = nation_df[nation_df["Name"] == nation_name].iloc[0]
            exec_row = exec_df[exec_df["Name"] == exec_name].iloc[0]
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
                cols[i % num_cols], item_data, is_selected, on_click_ne, f"ne_{i}"
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
                    st.image(player_data["国家アイコン"])
                st.write(f"**国家:** {player_data['国家']}")
            with col2:
                if player_data["重役アイコン"]:
                    st.image(player_data["重役アイコン"])
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
        )
        if game_id:
            st.success("ドラフト結果を保存しました！")
            st.balloons()
            reset_game_setup()
            st.session_state.screen = "landing"
            st.session_state.active_game = load_latest_game_from_sheet()
            st.rerun()


def show_score_input_screen():
    st.title("スコア入力")

    active_game_data = st.session_state.active_game
    if not active_game_data:
        st.error("スコア入力対象のゲームが見つかりません。")
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
        if contract_df is not None:
            show_setup_screen(contract_df)
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
    elif screen == "score_input":
        show_score_input_screen()
    else:
        st.session_state.screen = "landing"
        st.rerun()


if __name__ == "__main__":
    main()
