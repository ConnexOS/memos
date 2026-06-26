"""重建 ChromaDB project_memory 集合（优化版）"""
import chromadb
import pickle
import time
from sentence_transformers import SentenceTransformer
from memos.config import config

t0 = time.time()
data = pickle.load(open('memdb_backup.pkl', 'rb'))
print(f'Loaded {len(data["ids"])} entries from backup ({time.time()-t0:.1f}s)', flush=True)

client = chromadb.PersistentClient(path='memdb')
if 'project_memory' in [c.name for c in client.list_collections()]:
    client.delete_collection('project_memory')
new_col = client.create_collection('project_memory')
print(f'Collection created ({time.time()-t0:.1f}s)', flush=True)

# Single batch encoding
encoder = SentenceTransformer(config.model.path)
print(f'Model loaded, encoding {len(data["documents"])} docs...', flush=True)
all_embs = encoder.encode(data['documents'], show_progress_bar=True)
print(f'Encoding done ({time.time()-t0:.1f}s)', flush=True)

# Add in batches
total = len(data['ids'])
batch_size = 200
for i in range(0, total, batch_size):
    end = min(i + batch_size, total)
    new_col.add(
        ids=data['ids'][i:end],
        documents=data['documents'][i:end],
        metadatas=data['metadatas'][i:end],
        embeddings=all_embs[i:end].tolist(),
    )
    print(f'  Added {end}/{total} ({time.time()-t0:.1f}s)', flush=True)

col = client.get_collection('project_memory')
cnt = col.count()
print(f'Final count: {cnt} ({time.time()-t0:.1f}s)', flush=True)
assert cnt == total, f'Count mismatch: {cnt} != {total}'

# Verify query works
from sentence_transformers import SentenceTransformer
vec = encoder.encode('test').tolist()
results = col.query(query_embeddings=[vec], n_results=5, where={'project_id': 'd0ff92fa'})
print(f'Query OK: {len(results["ids"][0])} results', flush=True)
print('DONE', flush=True)
