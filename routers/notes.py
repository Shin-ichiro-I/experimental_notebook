import os
import uuid
from collections import defaultdict
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Depends, status, Query
from py2neo import Graph, Node as Py2NeoNode, Relationship, Subgraph
from py2neo.errors import Neo4jError
from models import (
    ExperimentCreate, 
    ExperimentDetails, 
    ExperimentInfo, 
    GraphResponse, 
    Node as PydanticNode,
    Edge as PydanticEdge,
    Folder
)
from typing import List, Dict, Optional

load_dotenv()
router = APIRouter()

# --- データベース接続 (変更なし) ---
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
    except Neo4jError as db_error:
        raise HTTPException(status_code=503, detail=f"Database error: {db_error}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected server error occurred: {e}")

# --- APIエンドポイント ---

@router.post("/experiments/", status_code=status.HTTP_201_CREATED, response_model=ExperimentDetails)
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
        nodes_to_create = [exp_node]
        node_map: Dict[str, Py2NeoNode] = {}

        for node_data in exp_data.nodes:
            props = node_data.properties.model_dump(by_alias=True)
            data_node = Py2NeoNode(
                node_data.category,
                id=node_data.id,
                **props
            )
            nodes_to_create.append(data_node)
            node_map[node_data.id] = data_node
        
        subgraph = Subgraph(nodes_to_create)
        tx.create(subgraph)

        for node_obj in subgraph.nodes:
            if "Experiment" not in node_obj.labels:
                 tx.create(Relationship(exp_node, "CONTAINS", node_obj))

        for edge_data in exp_data.edges:
            start_node = node_map.get(edge_data.source_id)
            end_node = node_map.get(edge_data.target_id)
            if start_node and end_node:
                tx.create(Relationship(start_node, edge_data.type, end_node))
        
        graph.commit(tx)
    except Exception as e:
        graph.rollback(tx)
        raise HTTPException(status_code=500, detail=f"Failed to create note: {e}")
    
    # ▼▼▼▼▼ ここを修正 ▼▼▼▼▼
    # model_dumpに by_alias=True を追加して、正しいキー名で辞書を作成
    response_data = exp_data.model_dump(by_alias=True)
    response_data['id'] = new_uuid
    return ExperimentDetails(**response_data)
    # ▲▲▲▲▲ ここを修正 ▲▲▲▲▲


# ▼▼▼▼▼ この関数を全面的に修正 ▼▼▼▼▼
@router.get("/experiments/{exp_uuid}", response_model=ExperimentDetails)
async def get_note_by_uuid(exp_uuid: uuid.UUID, graph: Graph = Depends(get_db_session)):
    """UUIDで単一の実験ノートを取得する"""
    
    # ▼▼▼▼▼ このクエリを最終版に修正 ▼▼▼▼▼
    query = """
    // 1. 指定されたIDの実験ノート(exp)と、それに含まれる全てのノード(n)を見つける
    MATCH (exp:Experiment {id: $exp_uuid})-[:CONTAINS]->(n)
    // 2. 見つけたノードを一度リスト(nodes)にまとめる
    WITH exp, collect(n) AS nodes
    // 3. リスト内のノードをn1とn2として、全ての組み合わせを調べる
    UNWIND nodes AS n1
    UNWIND nodes AS n2
    // 4. n1からn2へのリレーションシップ(r)があれば、それを全て見つける
    OPTIONAL MATCH (n1)-[r]->(n2)
    // 5. 最終的にexp, nodesリスト, そして見つかったedgesリストを返す
    RETURN exp, nodes, collect(DISTINCT r) AS edges
    """
    # ▲▲▲▲▲ このクエリを最終版に修正 ▲▲▲▲▲

    result = graph.run(query, exp_uuid=str(exp_uuid)).data()

    if not result:
        raise HTTPException(status_code=404, detail=f"Experiment with UUID '{exp_uuid}' not found")

    res = result[0]

    if not res.get('exp'):
        raise HTTPException(status_code=404, detail="Experiment data is incomplete or corrupted")
    
    exp_node = res['exp']
    nodes_list = res.get('nodes') or []
    edges_list = res.get('edges') or []
    
    # (ここから下のデータ整形ロジックは変更ありません)
    nodes_for_response = []
    for n in nodes_list:
        if not n: 
            continue
        category = next((label for label in n.labels if label != "Experiment"), "Unknown")
        props = {k: v for k, v in dict(n).items() if k != 'id'}
        nodes_for_response.append(PydanticNode(
            id=n.get('id'),
            category=category,
            properties=props
        ))
    
    edges_for_response = [
        PydanticEdge(
            # ▼▼▼▼▼ ここを修正 ▼▼▼▼▼
            source_id=e.nodes[0]['id'],
            target_id=e.nodes[1]['id'],
            # ▲▲▲▲▲ ここを修正 ▲▲▲▲▲
            type=list(e.types())[0] if e.types() else "RELATED_TO"
        ) for e in edges_list
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
# ▲▲▲▲▲ この関数を全面的に修正 ▲▲▲▲▲


@router.get("/projects/", response_model=List[str], summary="Get all project names")
async def get_project_list(graph: Graph = Depends(get_db_session)):
    # (この関数は変更なし)
    query = "MATCH (e:Experiment) RETURN DISTINCT e.project_name AS project_name ORDER BY project_name"
    results = graph.run(query).data()
    return [record['project_name'] for record in results]

@router.get("/projects/{project_name}/folders/", response_model=List[Folder], summary="Get folder tree for a project")
async def get_folder_tree(project_name: str, graph: Graph = Depends(get_db_session)):
    # (この関数は変更なし)
    query = "MATCH (e:Experiment {project_name: $project_name}) RETURN e.folder_path AS path"
    results = graph.run(query, project_name=project_name).data()
    tree = defaultdict(dict)
    for record in results:
        path = record['path']
        parts = [part for part in path.split('/') if part]
        current_level = tree
        full_path = ""
        for part in parts:
            full_path += f"/{part}"
            if part not in current_level:
                current_level[part] = {"name": part, "path": full_path, "children": {}}
            current_level = current_level[part]["children"]
    def dict_to_folder_list(d: dict) -> List[Folder]:
        folder_list = []
        for key, value in d.items():
            children = dict_to_folder_list(value["children"])
            folder_list.append(Folder(name=value["name"], path=value["path"], children=children))
        return folder_list
    return dict_to_folder_list(tree)

@router.get("/experiments/", response_model=List[ExperimentInfo])
async def list_or_search_experiments(
    q: Optional[str] = None,
    project_name: Optional[str] = None,
    folder_path: Optional[str] = None,
    graph: Graph = Depends(get_db_session)
):
    # (この関数は変更なし)
    if q:
        query = "MATCH (s:Substances)--(exp:Experiment) WHERE s.`Node Name` CONTAINS $q OR s.`CAS RN` = $q RETURN exp"
        results = graph.run(query, q=q).data()
        return [record['exp'] for record in results]
    elif project_name and folder_path:
        query = "MATCH (exp:Experiment) WHERE exp.project_name = $project_name AND exp.folder_path STARTS WITH $folder_path RETURN exp"
        results = graph.run(query, project_name=project_name, folder_path=folder_path).data()
        return [record['exp'] for record in results]
    else:
        results = graph.nodes.match("Experiment")
        return list(results)
    
@router.delete("/experiments/{exp_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note_by_uuid(exp_uuid: uuid.UUID, graph: Graph = Depends(get_db_session)):
    # (この関数は変更なし)
    query = "MATCH (exp:Experiment {id: $exp_uuid}) DETACH DELETE exp"
    result = graph.run(query, exp_uuid=str(exp_uuid))
    if result.stats()['nodes_deleted'] == 0:
         raise HTTPException(status_code=404, detail="Experiment not found to delete")
    return None

@router.put("/experiments/{exp_uuid}", status_code=status.HTTP_200_OK, response_model=ExperimentDetails)
async def update_note(exp_uuid: uuid.UUID, exp_data: ExperimentCreate, graph: Graph = Depends(get_db_session)):
    # 既存の実験ノートを更新する（全データ置き換え）
    tx = graph.begin()
    try:
        # 1. 更新対象のExperimentノードを見つける
        exp_node = graph.nodes.match("Experiment", id=str(exp_uuid)).first()
        if not exp_node:
            raise HTTPException(status_code=404, detail="Experiment not found to update!")
        
        # 2. Experimentノードのメタデータを更新
        exp_node.update(
            project_name=exp_data.project_name,
            folder_path=exp_data.folder_path,
            experiment_name=exp_data.experiment_name,
            registrant=exp_data.registrant,
            registration_date=exp_data.registration_date.isoformat()
        )
        tx.push(exp_node)

        # 3. 古いフローチャートノードをすべて削除
        query = "MATCH(exp:Experiment {id: $exp_uuid})-[:CONTAINS]->(n) DETACH DELETE n"
        tx.run(query, exp_uuid=str(exp_uuid))

        # 4. 新しいフローチャートを作成（create_noteのロジックを再利用）
        node_map: Dict[str, Py2NeoNode] = {}
        for node_data in exp_data.nodes:
            props = node_data.properties.model_dump(by_alias=True)
            data_node = Py2NeoNode(node_data.category, id=node_data.id, **props)
            tx.create(data_node)
            tx.create(Relationship(exp_node, "CONTAINS", data_node))
            node_map[node_data.id] = data_node

        for edge_data in exp_data.edges:
            start_node = node_map.get(edge_data.source_id)
            end_node = node_map.get(edge_data.target_id)
            if start_node and end_node:
                tx.create(Relationship(start_node, edge_data.type, end_node))

        graph.commit(tx)

        # 5. 更新後の完全なデータを返す
        return await get_note_by_uuid(exp_uuid, graph)
    
    except Exception as e:
        if tx:
            graph.rollback(tx)
        raise HTTPException(status_code=500, detail=f"Failed to update note: {e}")
    