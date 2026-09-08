//! HNSW vector index for semantic search.
//! Wraps instant-distance for approximate nearest neighbor search.

use instant_distance::{HnswMap, MapItem, Search};
use serde::{Serialize, Deserialize};
use std::collections::HashMap;

/// 384-dimensional embedding (all-MiniLM-L6-v2 compatible).
pub type Embedding = [f32; 384];

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct VectorNode {
    pub cid: String,
    pub embedding: Embedding,
    pub text: String,
}

impl MapItem for VectorNode {
    type Point = Embedding;

    fn point(&self) -> &Self::Point {
        &self.embedding
    }
}

/// HNSW index for fast approximate nearest neighbor search.
pub struct VectorIndex {
    index: Option<HnswMap<Embedding, VectorNode>>,
    nodes: Vec<VectorNode>,
}

impl VectorIndex {
    pub fn new() -> Self {
        Self { index: None, nodes: Vec::new() }
    }

    pub fn add(&mut self, node: VectorNode) {
        self.nodes.push(node);
    }

    pub fn build(&mut self) {
       