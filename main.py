from py2neo import Graph

# Neo4jへの接続
graph = Graph("bolt://localhost:7687", auth=("neo4j", "your_password"))