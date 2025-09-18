import uuid
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from datetime import date

class Node(BaseModel):
    """グラフのノードを表すモデル"""
    id: str = Field(..., description="ノードの一意なカスタムID")
    # 変更点: categoryは単一の文字列であることを明確化
    category: str = Field(..., description="ノードのカテゴリ (例: Substances, Processing)")
    properties: Dict[str, Any] = {}

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
    nodes: List[Node]
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