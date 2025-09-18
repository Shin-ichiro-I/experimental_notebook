import os
import uuid
from datetime import date
from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from py2neo import Graph
from py2neo.errors import ServiceUnavailable
from dotenv import load_dotenv
from typing import Union, Literal

# .envファイルから環境変数を読み込む
load_dotenv()

# ---環境変数から設定を読み込み---
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Neo4jへの接続
try:
    graph = Graph(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
except ServiceUnavailable:
    print("データベースに接続できません。URI、ユーザー名、パスワードを確認してください。")
    graph = None

# FastAPIアプリケーションのインスタンスを作成
app = FastAPI()

# ---CORS設定---
origins = [
    "null", # ローカルファイルからのアクセスを許可
    "http://localhost",
    "http://localhost:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---APIのデータモデルを定義---
class SubstanceProperties(BaseModel):
    node_name: str = Field(..., alias="Node Name")
    node_type: str | None = Field(None, alias="Node Type")
    cas_rn: str | None = Field(None, alias="CAS RN")
    smiles: str | None = Field(None, alias="SMILES")
    weight: str | None = Field(None, alias="Weight")
    volume: str | None = Field(None, alias="Volume")
    note: str | None = Field(None, alias="Note")

class ProcessingProperties(BaseModel):
    node_name: str = Field(..., alias="Node Name")
    note: str | None = Field(None, alias="Note")

class MeasurementProperties(BaseModel):
    node_name: str = Field(..., alias="Node Name")
    note: str | None = Field(None, alias="Note")

class OthersProperties(BaseModel):
    node_name: str = Field(..., alias="Node Name")
    note: str | None = Field(None, alias="Note")

PropertyTypes = Union[SubstanceProperties, ProcessingProperties, MeasurementProperties, OthersProperties]

class NodeData(BaseModel):
    id: str
    category: Literal["Substances", "Processing", "Measurement", "Others"]
    properties: PropertyTypes

class EdgeData(BaseModel):
    source_id: str
    target_id: str
    label: str = Field(..., alias="type")

class FlowchartData(BaseModel):
    project_name: str
    folder_path: str = "/"
    experiment_name: str
    registrant: str
    registration_date: date = Field(default_factory=date.today)
    nodes: list[NodeData]
    edges: list[EdgeData]

class ExperimentDetails(FlowchartData):
    project_id: str
    experiment_id: str

class ExperimentInfo(BaseModel):
    id: str
    name: str
    registrant: str
    registration_date: date

# --- APIエンドポイント ---
@app.post("/create_note/", status_code=status.HTTP_201_CREATED)
def create_note(data: FlowchartData):
    if graph is None:
        raise HTTPException(status_code=503, detail="データベース接続ができません。")
    tx = None
    try:
        tx = graph.begin()
        tx.run("MERGE (p:Project {name: $name})", name=data.project_name)
        parent_node_var = 'p'
        parent_name = data.project_name
        path_parts = [part for part in data.folder_path.split('/') if part]
        for i, folder_name in enumerate(path_parts):
            current_var = f'f{i}'
            if parent_node_var == 'p':
                query = f"MATCH (p:Project) WHERE p.name = $p_name MERGE (p)-[:CONTAINS]->({current_var}:Folder {{name: $f_name}})"
                tx.run(query, p_name=parent_name, f_name=folder_name)
            else:
                query = f"MATCH ({parent_node_var}:Folder) WHERE {parent_node_var}.name = $p_name MERGE ({parent_node_var})-[:CONTAINS]->({current_var}:Folder {{name: $f_name}})"
                tx.run(query, p_name=parent_name, f_name=folder_name)
            parent_node_var = current_var
            parent_name = folder_name
        exp_id = str(uuid.uuid4())
        tx.run("""
               CREATE (e:Experiment {id: $id, name: $name, registrant: $registrant, registration_date: $reg_date})
               """,
               id=exp_id, name=data.experiment_name, registrant=data.registrant,
               reg_date=data.registration_date.isoformat())
        if not path_parts:
            parent_match_query = "MATCH (p:Project) WHERE p.name = $p_name"
            parent_var_final = "p"
        else:
            parent_match_query = f"MATCH ({parent_node_var}:Folder) WHERE {parent_node_var}.name = $p_name"
            parent_var_final = parent_node_var
        tx.run(f"{parent_match_query} MATCH (e:Experiment) WHERE e.id = $e_id MERGE ({parent_var_final})-[:CONTAINS]->(e)", p_name=parent_name, e_id=exp_id)
        for node in data.nodes:
            query = f"MERGE (n:{node.category} {{id: $id}}) SET n += $properties"
            tx.run(query, id=node.id, properties=node.properties.model_dump(by_alias=True))
            tx.run("""
                MATCH (e:Experiment) WHERE e.id = $e_id
                MATCH (n) WHERE n.id = $n_id
                MERGE (e)-[:HAS_NODE]->(n)
                """,
                e_id=exp_id, n_id=node.id)
        for edge in data.edges:
            query = f"MATCH (a) WHERE a.id = $source_id MATCH (b) WHERE b.id = $target_id MERGE (a)-[r:`{edge.label}`]->(b)"
            tx.run(query, source_id=edge.source_id, target_id=edge.target_id)
        tx.commit()
        return {"experiment_id": exp_id, "message": "Note created successfully."}
    except Exception as e:
        if tx:
            tx.rollback()
        raise HTTPException(status_code=500, detail=f"データベース処理中にエラーが発生しました: {e}")

@app.get("/note/{exp_id}", response_model=ExperimentDetails)
def get_note(exp_id: str):
    if graph is None:
        raise HTTPException(status_code=503, detail="データベース接続ができません。")
    query = """
        MATCH (e:Experiment {id: $exp_id})
        MATCH (p:Project)-[:CONTAINS*]->(e)
        WITH p, e
        OPTIONAL MATCH (e)-[:HAS_NODE]->(n)
        OPTIONAL MATCH (n)-[r]->(m)
        WHERE (e)-[:HAS_NODE]->(m)
        RETURN p, e, collect(distinct n) as nodes, collect(distinct r) as edges
    """
    result = graph.run(query, exp_id=exp_id).data()
    if not result or not result[0]['e']:
        raise HTTPException(status_code=404, detail="指定された実験ノートが見つかりません。")
    record = result[0]
    project_node = record['p']
    exp_node = record['e']
    nodes_list = record['nodes'] if record['nodes'] is not None else []
    edges_list = record['edges'] if record['edges'] is not None else []
    property_model_map = {"Substances": SubstanceProperties, "Processing": ProcessingProperties, "Measurement": MeasurementProperties, "Others": OthersProperties}
    formatted_nodes = []
    for node in nodes_list:
        labels = [l for l in node.labels if l != 'Resource']
        category = labels[0] if labels else "Others"
        props = dict(node)
        node_id = props.pop('id', None)
        PropModel = property_model_map.get(category, OthersProperties)
        formatted_props = PropModel.model_validate(props, from_attributes=True)
        formatted_nodes.append({"id": node_id, "category": category, "properties": formatted_props})
    formatted_edges = []
    for edge in edges_list:
        formatted_edges.append({
            "source_id": edge.start_node['id'],
            "target_id": edge.end_node['id'],
            "label": type(edge).__name__
        })
    return {
        "project_id": str(project_node.identity),
        "project_name": project_node['name'],
        "experiment_id": exp_node['id'],
        "experiment_name": exp_node['name'],
        "registrant": exp_node['registrant'],
        "registration_date": exp_node['registration_date'],
        "nodes": formatted_nodes, "edges": formatted_edges
    }

@app.get("/experiments/", response_model=list[ExperimentInfo])
def list_experiments(project_name: str, folder_path: str = "/"):
    if graph is None:
        raise HTTPException(status_code=503, detail="データベース接続ができません。")
    params = {"p_name": project_name}
    path_parts = [part for part in folder_path.split('/') if part]
    if not path_parts:
        query = "MATCH (p:Project {name: $p_name})-[:CONTAINS]->(e:Experiment) RETURN e.id as id, e.name as name, e.registrant as registrant, e.registration_date as registration_date ORDER BY e.registration_date DESC"
    else:
        path_pattern = "(p:Project)"
        for i in range(len(path_parts)):
            path_pattern += f"-[:CONTAINS]->(f{i}:Folder)"
        path_pattern += "-[:CONTAINS]->(e:Experiment)"
        where_clauses = ["p.name = $p_name"]
        for i, folder_name in enumerate(path_parts):
            param_name = f"f_name_{i}"
            where_clauses.append(f"f{i}.name = ${param_name}")
            params[param_name] = folder_name
        where_str = " AND ".join(where_clauses)
        # ▼▼▼▼▼ ここが最後の修正点です ▼▼▼▼▼
        query = f"""
            MATCH {path_pattern}
            WHERE {where_str}
            RETURN e.id as id, e.name as name, e.registrant as registrant, e.registration_date as registration_date
            ORDER BY e.registration_date DESC
            """
        # ▲▲▲▲▲ ここが最後の修正点です ▲▲▲▲▲
    try:
        result = graph.run(query, **params).data()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"データベースクエリの実行中にエラーが発生しました：{e}")

@app.get("/search/substances/", response_model=list[ExperimentInfo])
def search_experiments_by_substance(cas_rn: str):
    if graph is None:
        raise HTTPException(status_code=503, detail="データベース接続ができません。")
    query = "MATCH (s:Substances) WHERE s.cas_rn = $cas_rn MATCH (s)<-[:HAS_NODE]-(e:Experiment) RETURN e.id as id, e.name as name, e.registrant as registrant, e.registration_date as registration_date ORDER BY e.registration_date DESC"
    result = graph.run(query, cas_rn=cas_rn).data()
    return result

@app.put("/note/{exp_id}", status_code=status.HTTP_200_OK)
def update_note(exp_id: str, data: FlowchartData):
    if graph is None:
        raise HTTPException(status_code=503, detail="データベース接続ができません。")
    tx = None
    try:
        tx = graph.begin()
        result_tx = tx.run("""
            MATCH (e:Experiment {id: $exp_id})
            SET e.name = $name, e.registrant = $registrant, e.registration_date = $reg_date
            """,
            exp_id=exp_id, name=data.experiment_name,
            registrant=data.registrant, reg_date=data.registration_date.isoformat())
        if result_tx.stats()['properties_set'] == 0:
            tx.rollback()
            raise HTTPException(status_code=404, detail="指定された実験ノートが見つかりません。")
        tx.run("MATCH (e:Experiment {id: $exp_id})-[:HAS_NODE]->(n) DETACH DELETE n", exp_id=exp_id)
        for node in data.nodes:
            query = f"MERGE (n:{node.category} {{id: $id}}) SET n += $properties"
            tx.run(query, id=node.id, properties=node.properties.model_dump(by_alias=True))
            tx.run("""
                MATCH (e:Experiment {id: $e_id})
                MATCH (n {id: $n_id})
                MERGE (e)-[:HAS_NODE]->(n)
                """,
                e_id=exp_id, n_id=node.id)
        for edge in data.edges:
            query = f"MATCH (a {{id: $source_id}}), (b {{id: $target_id}}) MERGE (a)-[r:`{edge.label}`]->(b)"
            tx.run(query, source_id=edge.source_id, target_id=edge.target_id)
        tx.commit()
        return {"experiment_id": exp_id, "message": "Note updated successfully."}
    except Exception as e:
        if tx:
            tx.rollback()
        raise HTTPException(status_code=500, detail=f"データベース処理中にエラーが発生しました: {e}")

@app.delete("/note/{exp_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(exp_id: str):
    if graph is None:
        raise HTTPException(status_code=503, detail="データベース接続ができません。")
    query = "MATCH (e:Experiment {id: $exp_id}) OPTIONAL MATCH (e)-[:HAS_NODE]->(n) DETACH DELETE e, n"
    try:
        result = graph.run(query, exp_id=exp_id)
        if result.stats()['nodes_deleted'] == 0:
            raise HTTPException(status_code=404, detail="指定された実験ノートが見つかりません。")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"データベース処理中にエラーが発生しました：{e}")

