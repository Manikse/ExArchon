//! cmg-storage — CMG persistence layer.
//! redb graph storage, HNSW vector index, tiered compaction.

use std::collections::HashMap;
use std::path::Path;
use redb::{Database, ReadableTable, TableDefinition};
use cmg_core::{CID, Atom, Edge};
use serde::{Serialize, Deserialize};
use thiserror::Error;

pub mod hnsw;
pub mod compaction;

#[derive(Error, Debug)]
pub enum StorageError {
    #[error("redb error: {0}")]
    Redb(#[from] redb::Error),
    #[error("key not found: {0}")]
    NotFound(String),
    #[error("serialization error: {0}")]
    Serde(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, StorageError>;

const ATOMS_TABLE: TableDefinition<&[u8], &[u8]> = TableDefinition::new("atoms");
const EDGES_TABLE: TableDefinition<&[u8], &[u8]> = TableDefinition::new("edges");
const META_TABLE: TableDefinition<&str, &[u8]> = TableDefinition::new("meta");

/// ACID graph storage backed by redb.
pub struct GraphStore {
    db: Database,
}

impl GraphStore {
    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self> {
        let db = Database::create(path)?;
        let tx = db.begin_write()?;
        {
            let _ = tx.open_table(ATOMS_TABLE)?;
            let _ = tx.open_table(EDGES_TABLE)?;
            let _ = tx.open_table(META_TABLE)?;
        }
        tx.commit()?;
        Ok(Self { db })
    }

    pub fn insert_atom(&self, atom: &Atom) -> Result<()> {
        let tx = self.db.begin_write()?;
        {
            let mut table = tx.open_table(ATOMS_TABLE)?;
            let key = atom.cid.as_bytes();
            let value = serde_json::to_vec(atom)?;
            table.insert(key.as_slice(), value.as_slice())?;
        }
        tx.commit()?;
        Ok(())
    }

    pub fn get_atom(&self, cid: &CID) -> Result<Option<Atom>> {
        let tx = self.db.begin_read()?;
        let table = tx.open_table(ATOMS_TABLE)?;
        match table.get(cid.as_bytes().as_slice())? {
            Some(data) => {
                let atom: Atom = serde_json::from_slice(data.value())?;
                Ok(Some(atom))
            }
            None => Ok(None),
        }
    }

    pub fn insert_edge(&self, edge: &Edge) -> Result<()> {
        let tx = self.db.begin_write()?;
        {
            let mut table = tx.open_table(EDGES_TABLE)?;
            let key = edge.source.as_bytes();
            let value = serde_json::to_vec(edge)?;
            table.insert(key.as_slice(), value.as_slice())?;
        }
        tx.commit()?;
        Ok(())
    }

    pub fn atom_count(&self) -> Result<u64> {
        let tx = self.db.begin_read()?;
        let table = tx.open_table(ATOMS_TABLE)?;
        Ok(table.len()?)
    }

    pub fn compact(&self) -> Result<()> {
        log::info!("Compaction requested");
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StoreStats {
    pub atoms: u64,
    pub edges: u64,
    pub file_size_mb: f64,
}