import os
import uuid
from datetime import date
from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, Field
from py2neo import Graph
from py2neo.errors import ServiceUnavailable
from dotenv import load_dotenv
from typing import Union, Literal

# Load environmental variables form .env file
load_dotenv()

# ---Read settings from environmental variables---
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Connection to Neo4j
try:
    graph = Graph(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
except ServiceUnavailable:
    print("データベースに接続できません。URI、ユーザー名、パスワードを確認してください。")
    graph = None


# Creation of instance of FastAIP apprication
app = FastAPI()

# ---Definition of API data model---
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
    # TO DO: add other difinitions

class MeasurementProperties(BaseModel):
    node_name: str = Field(..., alias="Node Name")
    note: str | None = Field(None, alias="Note")
    # TO DO: add other difinitions

class OthersProperties(BaseModel):
    node_name: str = Field(..., alias="Node Name")
    note: str | None = Field(None, alias="Note")
    # TO DO: add other difinitions

# Making Pydantic model unions reusable
PropertyType = Union[SubstanceProperties, ProcessingProperties, MeasurementProperties, OthersProperties]

class NodeData(BaseModel):
    id: str #ID to uniquely identify a node ("Substances", "Measurement", "Processing", "Others")
    category: Literal["Substances", "Processing", "Measurement", "Others"]
    properties: PropertyType

class EdgeData(BaseModel):
    source_id: str
    target_id: str # ("Chemical Treatment", "Phsycal Treatment", "Data analysis", etc.)
    label: str = Field(..., alias="type") # API input also allows the key name "type"

class FlowchartData(BaseModel):
    project_name: str
    folder_path: str = "/"
    experiment_name: str
    registrant: str
    registration_date: date = Field(default_factory=date.today)
    nodes: list[NodeData]
    edges: list[EdgeData]

class ExperimentDetails(FlowchartData):
    project_id: str # internal ID of Neo4j
    experiment_id: str

class ExperimentInfo(BaseModel):
    id: str
    name: str
    registrant: str
    registration_date: date

# --- API endpoint ---
@app.post("/create_note/", status_code=201)
def create_note(data: FlowchartData):
    if graph is None:
        raise HTTPException(status_code=503, detail="データベース接続ができません。")


    # Start transaction processing
    tx = None
    try:
        tx = graph.begin()

        # 1. Search "Project Node", if non, then create (MERGE)
        tx.run("MERGE (p:Project {name: $name})", name=data.project_name)

        # 2. folder structure MERGE
        parent_node_var = 'p' # First parent is the Project
        parent_name = data.project_name
        # Split the path at '/' and remove empty elements
        path_parts = [part for part in data.folder_path.split('/') if part]

        for i, folder_name in enumerate(path_parts):
            current_var = f'f{i}'
            # MERGW Folder connected to a parent node (Project or Parent Folder)
            if parent_node_var == 'p':
                query = f"""
                    MATCH (p:Project) WHERE p.name = $p_name
                    MERGE (p)-[:CONTAINS]->({current_var}:Folder {{name: $f_name}})
                    """
                tx.run(query, p_name=parent_name, f_name=folder_name)
            else:
                query = f"""
                    MATCH ({parent_node_var}:Folder) WHERE {parent_node_var}.name = $p_name
                    MERGE ({parent_node_var})-[:CONTAINS]->({current_var}:Folder {{name: $f_name}})
                    """
                tx.run(query, p_name=parent_name, f_name=folder_name)

            # Update parent for next loop
            parent_node_var = current_var
            parent_name = folder_name

        # 3. Create new "Experiment Node" (CREATE)
        exp_id = str(uuid.uuid4())
        tx.run("""
               CREATE (e:Experiment {
               id: $id,
               name: $name,
               registrant: $registrant,
               registration_date: $reg_date
               })
               """,
               id=exp_id,
               name=data.experiment_name,
               registrant=data.registrant,
               reg_date=data.registration_date.isoformat()
               )
        
        # 4. Relate #Final parent" to Experiment
        if not path_parts: # if folder path is "/"
            parent_match_query = "MATCH (p:Project) WHERE p.name = $p_name"
            parent_var_final = "p"
        else:
            parent_match_query = f"MATCH ({parent_node_var}:Folder) WHERE {parent_node_var}.name = $p_name"
            parent_var_final = parent_node_var 
        
        tx.run(f"""
            {parent_match_query}
            MATCH (e:Experiment) WHERE e.id = $e_id
            MERGE ({parent_var_final})-[:CONTAINS]->(e)
            """,
            p_name=parent_name, e_id=exp_id
            )

        # 5. Create "Node of FlowChart", then combine "Experiment"
        for node in data.nodes:
            # Category of nodes are use as Neo4j's labels
            # Due to this process, it is possible to use query such a "MATCH(n:Substance)"
            query = f"MERGE (n:{node.category} {{id: $id}}) SET n += $properties"
            tx.run(query, id=node.id, properties=node.properties.model_dump(by_alias=True))

            # relete "Experiment" and each nodes
            tx.run("""
                MATCH (e:Experiment) WHERE e.id = $e_id
                MATCH (n) WHERE n.id = $n_id
                MERGE (e)-[:HAS_NODE]->(n)
                """,
                e_id=exp_id,
                n_id=node.id
                )

        # 6. Create relationships beteen nodes
        for edge in data.edges:
            query = f"""
                MATCH (a) WHERE a.id = $source_id
                MATCH (b) WHERE b.id = $target_id
                MERGE (a)-[r:`{edge.label}`]->(b)
            """
            tx.run(query, source_id=edge.source_id, target_id=edge.target_id)

        # commit after all process done successfully (save to database)
        tx.commit()
        return {"experiment_id": exp_id, "message": "Note created successfully in folder."}
    
    except Exception as e:
        # if error is occur, do rollback (Cancel all processes)
        if tx:
            tx.rollback()
        raise HTTPException(status_code=500, detail=f"データベース処理中にエラーが発生しました: {e}")


@app.get("/note/{exp_id}", response_model=ExperimentDetails)
def get_note(exp_id: str):
    if graph is None:
        raise HTTPException(status_code=503, detail="データベース接続ができません。")
    
    # get the Project Information
    query = """
        MATCH (p:Project)-[:CONTAINS*]->(e:Experiment {id: $exp_id})
        MATCH (e)-[:HAS_NODE]->(n)
        OPTIONAL MATCH (n)-[r]->(m)
        WHERE (e)-[:HAS_NODE]->(m) // relationships are restricted in the same experimental note
        RETURN p, e, collect(distinct n) as nodes, collect(distinct r) as edges
    """
    result = graph.run(query, exp_id=exp_id).data()

    if not result:
        raise HTTPException(status_code=404, detail="指定された実験ノートは見つかりません。")
    
    record = result[0]
    project_node = record['p']
    exp_node = record['e']
    nodes_list = record['nodes']
    edges_list = record['edges']

    # Reformatting data from the DB into structured Pydantic models
    property_model_map = {
        "Substances": SubstanceProperties,
        "Processing": ProcessingProperties,
        "Measurement": MeasurementProperties,
        "Others": OthersProperties,
    }

    # reformation in API response format
    formatted_nodes = []
    for node in nodes_list:
        labels = [l for l in node.labels if l != 'Resource']
        category = labels[0] if labels else "Others"
        props = dict(node)
        node_id = props.pop('id', None)

        # Validate and shape properties with a Pydantic model that corresponds to a category
        PropModel = property_model_map.get(category, OthersProperties)
        formatted_props = PropModel.model_validate(props, from_attributes=True)

        formatted_nodes.append({
            "id": node_id,
            "category": category,
            "properties": formatted_props
        })
    
    formatted_edges = []
    for edge in edges_list:
        formatted_edges.append({
            "source_id": edge.start_node['id'],
            "target_id": edge.end_node['id'],
            "type": type(edge).__name__
        })

    return {
        "project_id": str(project_node.identity),
        "project_name": project_node['name'],
        "experiment_id": exp_node['id'],
        "experiment_name": exp_node['name'],
        "registrant": exp_node['registrant'],
        "registration_date": exp_node['registration_date'],
        "nodes": formatted_nodes,
        "edges": formatted_edges        
    }

@app.get("/experiments/", response_model=list[ExperimentInfo])
def list_experiments(project_name: str, folder_path: str = "/"):
    if graph is None:
        raise HTTPException(status_code=503, detail="データベース接続ができません。")
    
    # A query to find an Experiment node from a specified project and folder path
    # The path traversal part references the logic in create_note
    path_parts = [part for part in folder_path.split('/') if part]

    if not path_parts: # if it is the root folder
        query = """
            MATCH (p:Project) WHERE p.name = $p_name
            MATCH (p)-[:CONTAINS]->(e:Experiment)
            RETURN e.id as id, e.name as name, e.registrant as registrant, e.registration_date as registration_date
            ORDER BY e.registration_date DESC
        """
        result = graph.run(query, p_name=project_name).data()
    else:
        # Dynamically generate MATCH clauses that traverse folder hierarchy
        match_clauses = ["(p:Project) WHERE p.name = $p_name"]
        for i, folder_name in enumerate(path_parts):
            match_clauses.append(f"(:Folder {{name: $f_name{i}}})")

        path_str = "-[:CONTAINS]->".join(match_clauses)

        query = f"""
            MATCH {path_str}-[:CONTAINS]->(e:Experiment)
            RETURN e.id as id, e.name as name, e.registrant as registrant, e.registration_date as registration_date
            ORDER BY e.registration_date DESC
        """
        params = {"p_name": project_name}
        for i , folder_name in enumerate(path_parts):
            params[f"f_name_{i}"] = folder_name

        result = graph.run(query, **params).data()

    return result


@app.get("/search/substances/", response_model=list[ExperimentInfo])
def search_experiments_by_substance(cas_rn: str):
    if graph is None:
        raise HTTPException(status_code=503, detail="データベース接続ができません。")
    
    # Find the Substance node with the specified CAS RN and perform a reverse lookup of the experiment containing it
    query = """
        MATCH (s:Substances) WHERE s.cas_rn = $cas_rn
        MATCH (s)<-[:HAS_NODE]-(e:Experiment)
        RETURN e.id as id, e.name as name, e.registrant as registrant, e.registration_date as registration_date
        ORDER BY e.registration_date DESC
        """
    result = graph.run(query, cas_rn=cas_rn).data()

    return result

@app.delete("/note/{exp_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(exp_id: str):
    if graph is None:
        raise HTTPException(status_code=503, detail="データベース接続ができません。")
    
    # A query to delete an Experiment node and all its associated flowchart nodes.
    # DETACH DELETE is a safe command that also deletes the relationships connected to the node.
    query = """
        MATCH (e:Experiment {id: $exp_id})
        OPTIONAL MATCH (e)-[:HAS_NODE]->(n)
        DETACH DELETE e, n
        """
    
    try:
        result = graph.run(query, exp_id=exp_id)
        # If nothing was deleted (ID does not exist), return a 404 error
        if result.stats()['nodes_deleted'] == 0:
            raise HTTPException(status_code=404, detail="指定された実験ノートが見つかりません。")
    
        # It is common practice not to return content if successful
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"データベース処理中にエラーが発生しました：{e}")
    

@app.put("/note/{exp_id}", status_code=status.HTTP_200_OK)
def update_note(exp_id: str, data:FlowchartData):
    if graph is None:
        raise HTTPException(status_code=503, detail="データベース接続ができません。")
    
    tx = None
    try:
        tx = graph.begin()

        # 1. Check if the experiment node to be updated exists and update the metadata
        result_tx = tx.run("""
            MATCH (e:Experiment {id: $exp_id})
            SET e.name = $name,
                e.registrant = $registrant,
                e.registration_data = $reg_data
            """,
            exp_id = exp_id, 
            name=data.experiment_name,
            registrant=data.registrant,
            reg_data=data.registration_date.isoformat())
        
        # 404 error if node not found and nothing updated
        if result_tx.stats()['properties_set'] == 0:
            tx.rollback()
            raise HTTPException(status_code=404, detail="指定された実験ノートが見つかりません。")
        
        # 2. Delete all old flowchart nodes
        tx.run("""
            MATCH (e:Experiment {id: $exp_id})-[:HAS_NODE]->(n)
            DETACH DELETE n
            """,
            exp_id=exp_id
            )

        # 3. Create and associate a new flowchart node (reuse the logic from create_note)
        for node in data.nodes:
            query = f"MERGE (n:{node.category} {{id: $id}}) SET n += $properties"
            tx.run(query, id=node.id, properties=node.properties.model_dump(by_alias=True))
            tx.run("""
                MATCH (e:Experiment {id: $e_id})
                MATCH (n {id: $n_id})
                MERGE (e)-[:HAS_NODE]->(n)
                """,
                e_id=exp_id, n_id=node.id)

        # 4. Create relationships between new nodes (reuse logic from create_note)
        for edge in data.edges:
            query = f"MATCH (a {{id: $source_id}}), (b {{id: $target_id}}) MERGE (a)-[r:`{edge.label}`]->(b)"
            tx.run(query, source_id=edge.source_id, target_id=edge.target_id)

        tx.commit()
        return {"experiment_id": exp_id, "message": "Note updated successfully."}
    
    except Exception as e:
        if tx:
            tx.rollback()
        raise HTTPException(status_code=500, detail=f"データベース処理中にエラーが発生しました：{e}")