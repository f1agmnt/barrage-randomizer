import streamlit as st
import gspread
import pandas as pd
import random
import os
import base64
from itertools import product

# --- 定数定義 ---
SPREADSHEET_KEY = "14sDX_7rw3WcGpWji59Ornhkx9G9obs-ZRn8sgqcs9yA"
NATION_SHEET = "国家マスタ"
EXECUTIVE_SHEET = "重役マスタ"
CONTRACT_SHEET = "初期契約マスタ"
SCORE_SHEET = "スコア記録"
IMAGE_DIR = "images"


# --- データ読み込みとキャッシュ ---
@st.cache_data(ttl=600)
def get_master_data(worksheet_name):
    """指定されたワークシートからデータを読み込み、DataFrameとして返す"""
    try:
        gc = gspread.service_account(filename="barrage20250822-703be3b5b5ed.json")
        sh = gc.open_by_key(SPREADSHEET_KEY)
        worksheet = sh.worksheet(worksheet_name)
        data = worksheet.get_all_values()
        headers = data[0]
        df_data = data[1:]
        return pd.DataFrame(df_data, columns=headers)
    except gspread.exceptions.SpreadsheetNotFound:
        st.error(
            f"スプレッドシートが見つかりません。キー '{SPREADSHEET_KEY}' を確認してください。"
        )
        return None
    except gspread.exceptions.WorksheetNotFound:
        st.error(f"ワークシート '{worksheet_name}' が見つかりません。")
        return None
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
    except Exception:
        return ""


# --- セッション管理 ---
def initialize_session_state():
    """セッション変数を初期化する"""
    if "screen" not in st.session_state:
        st.session_state.screen = "initial"

    if "game_setup" not in st.session_state:
        st.session_state.game_setup = {
            "player_count": 4,
            "player_names": [],
            "draft_candidate_count_option": "人数+1",
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


def show_initial_screen(nation_df, exec_df):
    """初期画面を描画する"""
    st.title("バラージ セットアップランダマイザ")

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
    """セットアップ画面を描画する"""
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
            st.error(
                "選択された国家または重役の数が、必要な候補数より少ないです。初期画面に戻って選択肢を増やすか、候補数を減らしてください。"
            )
            if st.button("初期画面に戻る"):
                st.session_state.screen = "initial"
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


def show_draft_screen(nation_df, exec_df):
    """ドラフト画面（通常ドラフト）を描画する"""
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

            picked_nation = selected_ne[0]
            picked_executive = selected_ne[1]
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
        ne_cols = st.columns(len(ne_candidates))
        for i, candidate in enumerate(ne_candidates):
            with ne_cols[i]:
                is_selected = candidate == setup_data["current_selection_ne"]
                with st.container(border=True):
                    nation_name, exec_name = candidate
                    nation_row = nation_df[nation_df["Name"] == nation_name]
                    exec_row = exec_df[exec_df["Name"] == exec_name]

                    # 国家アイコン
                    if not nation_row.empty and "IconURL" in nation_df.columns:
                        filename = nation_row["IconURL"].iloc[0]
                        if filename:
                            full_path = os.path.join(IMAGE_DIR, filename)
                            if os.path.exists(full_path):
                                st.image(image_to_data_url(full_path), width=200)
                    st.markdown(f"**{nation_name}**")
                    if not nation_row.empty and "Description" in nation_df.columns:
                        st.caption(nation_row["Description"].iloc[0])

                    # 重役アイコン
                    if not exec_row.empty and "IconURL" in exec_df.columns:
                        filename = exec_row["IconURL"].iloc[0]
                        if filename:
                            full_path = os.path.join(IMAGE_DIR, filename)
                            if os.path.exists(full_path):
                                st.image(image_to_data_url(full_path), width=200)
                    st.write(exec_name)
                    if not exec_row.empty and "Description" in exec_df.columns:
                        st.caption(exec_row["Description"].iloc[0])

                    if st.button(
                        "選択" if not is_selected else "解除",
                        key=f"ne_{i}",
                        use_container_width=True,
                        type="secondary" if not is_selected else "primary",
                    ):
                        setup_data["current_selection_ne"] = (
                            None if is_selected else candidate
                        )
                        st.rerun()

    st.divider()

    # --- [修正] 初期契約の表示 ---
    st.subheader("初期契約")
    contract_candidates = setup_data["contract_candidates"]
    if contract_candidates:
        contract_cols = st.columns(len(contract_candidates))
        for i, candidate in enumerate(contract_candidates):
            with contract_cols[i]:
                # IDで比較することで、より確実に選択状態を判定
                is_selected = (
                    setup_data["current_selection_contract"] is not None
                    and candidate["ID"]
                    == setup_data["current_selection_contract"]["ID"]
                )
                with st.container(border=True):
                    # 画像
                    if "ImageURL" in candidate and candidate["ImageURL"]:
                        full_path = os.path.join(IMAGE_DIR, candidate["ImageURL"])
                        if os.path.exists(full_path):
                            st.image(image_to_data_url(full_path), width=200)

                    # 名前
                    st.markdown(f"**{candidate['Name']}**")

                    # 説明 (もしあれば)
                    if "Description" in candidate and candidate["Description"]:
                        st.caption(candidate["Description"])

                    # 選択ボタン
                    if st.button(
                        "選択" if not is_selected else "解除",
                        key=f"contract_{i}",
                        use_container_width=True,
                        type="secondary" if not is_selected else "primary",
                    ):
                        setup_data["current_selection_contract"] = (
                            None if is_selected else candidate
                        )
                        st.rerun()


def get_icon_data_url(df, name, column_name="IconURL"):
    """DataFrameから指定された名前のアイコン画像のデータURLを取得する"""
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
    """ドラフト結果画面を描画する"""
    st.title("ドラフト結果")

    draft_order = st.session_state.game_setup["draft_order"]
    draft_results = st.session_state.game_setup["draft_results"]
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

            col1, col2 = st.columns([0.3, 0.7])
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

    if st.button("スコア入力へ", type="primary", use_container_width=True):
        st.session_state.screen = "score_input"
        st.rerun()


def show_score_input_screen():
    """スコア入力画面を描画する"""
    st.title("スコア入力")
    st.info("この画面は現在開発中です。")

    if st.button("スコア保存"):
        st.success("スコアが保存されました。（現在はダミーです）")

    if st.button("新規ゲーム", type="secondary"):
        st.session_state.confirm_reset = True

    if st.session_state.get("confirm_reset"):
        st.warning("すべての入力情報をリセットして最初の画面に戻りますか？", icon="⚠️")
        cols = st.columns(2)
        if cols[0].button(
            "はい、リセットします", use_container_width=True, type="primary"
        ):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            initialize_session_state()
            st.rerun()
        if cols[1].button("いいえ、戻ります", use_container_width=True):
            st.session_state.confirm_reset = False
            st.rerun()


# --- メイン処理 ---
def main():
    """アプリケーションのメインエントリポイント"""
    st.set_page_config(layout="wide", page_title="バラージ セットアップランダマイザ")

    initialize_session_state()

    nation_df = get_master_data(NATION_SHEET)
    exec_df = get_master_data(EXECUTIVE_SHEET)
    contract_df = get_master_data(CONTRACT_SHEET)

    if nation_df is None or exec_df is None or contract_df is None:
        st.error("マスターデータの読み込みに失敗しました。処理を停止します。")
        st.stop()

    screen = st.session_state.screen
    if screen == "initial":
        show_initial_screen(nation_df, exec_df)
    elif screen == "setup":
        show_setup_screen(contract_df)
    elif screen == "draft":
        show_draft_screen(nation_df, exec_df)
    elif screen == "draft_result":
        show_draft_result_screen(nation_df, exec_df)
    elif screen == "score_input":
        show_score_input_screen()
    else:
        st.session_state.screen = "initial"
        st.rerun()


if __name__ == "__main__":
    main()
