import os
import uuid
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Depends, status, Query
from py2neo import Graph, Node as Py2NeoNode, Relationship
from py2neo.errors import Neo4jError
from models import (
    ExperimentCreate, 
    ExperimentDetails, 
    ExperimentInfo, 
    GraphResponse, 
    Node as PydanticNode,
    Edge as PydanticEdge
)
from typing import List, Dict

load_dotenv()
router = APIRouter()

# --- データベース接続 (エラーハンドリングを改善) ---
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

def get_db_session():
    if not NEO4J_PASSWORD:
        raise HTTPException(status_code=500, detail="Database password is not configured.")
    try:
        graph = Graph(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        graph.run("RETURN 1")
        yield graph
    except HTTPException as http_exc:
        raise http_exc
    except Neo4jError as db_error:
        raise HTTPException(status_code=503, detail=f"Database error: {db_error}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected server error occurred: {e}")

# --- APIエンドポイント (UUID対応) ---

@router.post("/create_note/", status_code=status.HTTP_201_CREATED, response_model=ExperimentDetails)
async def create_note(exp_data: ExperimentCreate, graph: Graph = Depends(get_db_session)):
    new_uuid = uuid.uuid4()
    tx = graph.begin()
    try:
        exp_node = Py2NeoNode(
            "Experiment",
            id=str(new_uuid),
            project_name=exp_data.project_name,
            folder_path=exp_data.folder_path,
            experiment_name=exp_data.experiment_name,
            registrant=exp_data.registrant,
            registration_date=exp_data.registration_date.isoformat()
        )
        tx.create(exp_node)
        node_map: Dict[str, Py2NeoNode] = {}
        for node_data in exp_data.nodes:
            data_node = Py2NeoNode(
                node_data.category,
                id=node_data.id,
                **node_data.properties
            )
            tx.create(data_node)
            tx.create(Relationship(exp_node, "CONTAINS", data_node))
            node_map[node_data.id] = data_node
        for edge_data in exp_data.edges:
            start_node = node_map.get(edge_data.source_id)
            end_node = node_map.get(edge_data.target_id)
            if start_node and end_node:
                tx.create(Relationship(start_node, edge_data.type, end_node))
        graph.commit(tx)
    except Exception as e:
        graph.rollback(tx)
        raise HTTPException(status_code=500, detail=f"Failed to create note: {e}")
    
    response_data = exp_data.model_dump()
    response_data['id'] = new_uuid
    return ExperimentDetails(**response_data)

@router.get("/note/{exp_uuid}", response_model=ExperimentDetails)
async def get_note_by_uuid(exp_uuid: uuid.UUID, graph: Graph = Depends(get_db_session)):
    """UUIDで単一の実験ノートを取得する"""
    # 変更点1: クエリを改善し、実験に含まれるノードと、それらの間のリレーションシップのみを正確に取得
    query = """
    MATCH (exp:Experiment {id: $exp_uuid})
    OPTIONAL MATCH (exp)-->(n)
    WITH exp, collect(distinct n) as nodes
    UNWIND nodes as node
    OPTIONAL MATCH (node)-[r]-(m) WHERE m in nodes
    RETURN exp, nodes, collect(distinct r) as edges
    """
    result = graph.run(query, exp_uuid=str(exp_uuid)).data()

    # 変更点2: クエリ結果が空のリストでないかを確認
    if not result:
        raise HTTPException(status_code=404, detail=f"Experiment with UUID '{exp_uuid}' not found")

    # 変更点3: リストの最初の要素（インデックス0）を正しく取得
    res = result[0]

    if not res.get('exp'):
        raise HTTPException(status_code=404, detail="Experiment data is incomplete or corrupted")
    
    exp_node = res['exp']
    nodes_list = res.get('nodes',)
    edges_list = res.get('edges',)

    # 変更点4: Pydanticモデルへの変換ロジックを、py2neoオブジェクトの構造に合わせて完全に修正
    nodes_for_response = []
    for n in nodes_list:
        if not n: 
            continue
        # specific_labels = [label for label in n.labels if label not in ["Experiment", "Node"]]
        # category = specific_labels if specific_labels else "Unknown"
        category = next((label for label in n.labels if label not in ["Experiment", "Node"]), "Unknown")

        nodes_for_response.append(PydanticNode(
            id=n.get('id', ''),
            category=category,
            properties={k: v for k, v in dict(n).items() if k!= 'id'}
        ))
    
    edges_for_response = [
        PydanticEdge(
            source_id=e.start_node['id'],
            target_id=e.end_node['id'],
            type=type(e).__name__
        ) for e in edges_list if e and e.start_node and e.end_node
    ]

    return ExperimentDetails(
        id=uuid.UUID(exp_node['id']),
        project_name=exp_node['project_name'],
        folder_path=exp_node['folder_path'],
        experiment_name=exp_node['experiment_name'],
        registrant=exp_node['registrant'],
        registration_date=exp_node['registration_date'],
        nodes=nodes_for_response,
        edges=edges_for_response
    )


@router.get("/experiments/", response_model=List[ExperimentInfo])
async def get_all_experiments(graph: Graph = Depends(get_db_session)):
    """全ての実験ノートの概要をリストで取得する"""
    return list(graph.nodes.match("Experiment"))

@router.get("/search/substances/", response_model=List[ExperimentInfo])
async def search_experiments_by_substance(
    q: str = Query(..., description="Search query for substance name (Node Name) or CAS RN"),
    graph: Graph = Depends(get_db_session)
):
    """物質名やCAS RNで実験ノートを検索する"""
    query = """
    MATCH (s:Substances)--(exp:Experiment)
    WHERE s.`Node Name` CONTAINS $q OR s.`CAS RN` = $q
    RETURN exp
    """
    results = graph.run(query, q=q).data()
    return [record['exp'] for record in results]

@router.delete("/note/{exp_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note_by_uuid(exp_uuid: uuid.UUID, graph: Graph = Depends(get_db_session)):
    query = "MATCH (exp:Experiment {id: $exp_uuid}) DETACH DELETE exp"
    # (関連ノードも削除する場合は DETACH DELETE exp, n のクエリを使用)
    result = graph.run(query, exp_uuid=str(exp_uuid))
    if result.stats()['nodes_deleted'] == 0:
         raise HTTPException(status_code=404, detail="Experiment not found to delete")
    return None

@router.put("/note/{exp_id}", status_code=status.HTTP_200_OK, response_model=ExperimentDetails)
async def update_note(exp_id: str, exp_details: ExperimentCreate, graph: Graph = Depends(get_db_session)):
    """既存の実験ノートを更新する (全データ置き換え)"""
    # 既存のノートを削除
    await delete_note_by_uuid(exp_id, graph)
    # 新しいノートを作成
    return await create_note(exp_details, graph)

# --- 既存のエンドポイント ---
# (必要であれば残す)
# @router.get(
#     "/graph/{node_id}",
#     response_model=GraphResponse,
#     summary="Get graph from a specific node ID",
#     description="Retrieves the graph of nodes and relationships connected to a specific node ID."
# )
# async def get_note_graph(node_id: str, graph: Graph = Depends(get_db_session)):
#     query = "MATCH (n {id: $node_id})-[r]-(m) RETURN n, r, m"
#     results = graph.run(query, node_id=node_id).data()
#     if not results:
#         raise HTTPException(status_code=404, detail=f"Graph for node_id '{node_id}' not found.")
#     return convert_py2neo_to_graph_response(results)
