import uuid
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any, Union, Literal
from datetime import date

class Folder(BaseModel):
    name: str
    path: str
    children: List['Folder'] = []

# --- ▼▼▼ ノードのプロパティをカテゴリ別に定義 ▼▼▼ ---
class SubstanceProperties(BaseModel):
    model_config = ConfigDict(extra='allow') # 未知のフィールドを許可

    node_name: str = Field(..., alias="Node Name")
    node_type: str | None = Field(None, alias="Node Type")
    cas_rn: str | None = Field(None, alias="CAS RN")
    smiles: str | None = Field(None, alias="SMILES")
    weight: str | None = Field(None, alias="Weight")
    volume: str | None = Field(None, alias="Volume")
    note: str | None = Field(None, alias="Note")

class ProcessingProperties(BaseModel):
    model_config = ConfigDict(extra='allow') # 未知のフィールドを許可

    node_name: str = Field(..., alias="Node Name")
    note: str | None = Field(None, alias="Note")

class MeasurementProperties(BaseModel):
    model_config = ConfigDict(extra='allow') # 未知のフィールドを許可

    node_name: str = Field(..., alias="Node Name")
    note: str | None = Field(None, alias="Note")

class OthersProperties(BaseModel):
    model_config = ConfigDict(extra='allow') # 未知のフィールドを許可

    node_name: str = Field(..., alias="Node Name")
    note: str | None = Field(None, alias="Note")

PropertyTypes = Union[SubstanceProperties, ProcessingProperties, MeasurementProperties, OthersProperties]

class Node(BaseModel):
    """グラフのノードを表すモデル"""
    id: str = Field(..., description="ノードの一意なカスタムID")
    category: Literal["Substances", "Processing", "Measurement", "Others"]
    properties: PropertyTypes # 汎用の辞書から、カテゴリ別の厳密なモデルに変更

class Edge(BaseModel):
    """グラフのエッジ（リレーションシップ）を表すモデル"""
    source_id: str
    target_id: str
    type: str

class ExperimentCreate(BaseModel):
    """実験ノートの新規作成・更新時に受け取るデータモデル"""
    project_name: str
    folder_path: str
    experiment_name: str
    registrant: str
    registration_date: date
    nodes: List[Node] # 更新されたNodeモデルを使用
    edges: List[Edge]

class ExperimentDetails(ExperimentCreate):
    """APIが返す、完全な実験ノートのデータモデル"""
    id: uuid.UUID = Field(..., description="システムが生成する一意なID")

class ExperimentInfo(BaseModel):
    """実験ノートの概要を表すモデル"""
    id: uuid.UUID
    project_name: str
    folder_path: str
    experiment_name: str
    registrant: str
    registration_date: date

    class Config:
        from_attributes = True

class GraphResponse(BaseModel):
    """特定のノードIDに関連するグラフ構造のレスポンスモデル"""
    nodes: List[Node]
    edges: List[Edge]