# converters.py

from py2neo.data import Node as Py2NeoNode, Relationship as Py2NeoRelationship
from typing import List, Dict, Any
from models import Node, Edge, GraphResponse

def convert_py2neo_to_graph_response(records: List) -> GraphResponse:
    """
    py2neoのクエリ結果を、指定されたJSON形式に準拠したGraphResponseモデルに変換する。
    """
    processed_nodes: Dict[str, Node] = {} # キーを内部ID(int)からカスタムID(str)に変更
    response_edges: List[Edge] = []

    for record in records:
        n: Py2NeoNode = record.get('n')
        r: Py2NeoRelationship = record.get('r')
        m: Py2NeoNode = record.get('m')

        if not all([n, r, m]):
            continue

        # ノードを処理
        for node_obj in [n, m]:
            # ノードのプロパティからカスタムIDを取得
            custom_id = node_obj.get("id")
            if not custom_id:
                continue # IDがないノードはスキップ

            if custom_id not in processed_nodes:
                # Neo4jのラベルのリストから最初のものをcategoryとして使用
                category = list(node_obj.labels) if node_obj.labels else "Unknown"
                
                # 'id'と'category'に相当するプロパティをproperties辞書から削除
                props = dict(node_obj)
                props.pop("id", None) # propertiesにidが含まれないように削除

                processed_nodes[custom_id] = Node(
                    id=custom_id,
                    category=category,
                    properties=props
                )
        
        # エッジを処理
        source_node_id = r.start_node.get("id")
        target_node_id = r.end_node.get("id")

        if source_node_id and target_node_id:
            response_edges.append(
                Edge(
                    source_id=source_node_id,
                    target_id=target_node_id,
                    type=type(r).__name__ # リレーションシップの型名を'type'として使用
                )
            )

    return GraphResponse(
        nodes=list(processed_nodes.values()),
        edges=response_edges
    )