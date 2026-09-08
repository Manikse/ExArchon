//! cmg-core — Cognitive Memory Graph: core primitives.
//! CID, Arena allocator, Atom/Edge types.
//! no_std ready (with alloc).

#![cfg_attr(not(feature = "std"), no_std)]

extern crate alloc;

use alloc::vec::Vec;
use alloc::string::String;
use core::fmt;
use serde::{Serialize, Deserialize};

/// 32-byte Content Identifier using Blake3.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct CID([u8; 32]);

impl CID {
    pub fn new(data: &[u8]) -> Self {
        let hash = blake3::hash(data);
        Self(hash.into())
    }

    pub fn from_bytes(bytes: [u8; 32]) -> Self {
        Self(bytes)
    }

    pub fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }

    pub fn hex(&self) -> String {
        hex::encode(self.0)
    }
}

impl fmt::Debug for CID {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "CID({})", &self.hex()[..16])
    }
}

impl fmt::Display for CID {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.hex())
    }
}

/// A node in the Cognitive Memory Graph.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Atom {
    pub cid: CID,
    pub timestamp_ns: u64,
    pub importance: f32,   // 0.0 .. 1.0
    pub facts: Vec<String>,
    pub raw_content: Vec<u8>,
}

impl Atom {
    pub fn new(raw: Vec<u8>, importance: f32) -> Self {
        let cid = CID::new(&raw);
        Self {
            cid,
            timestamp_ns: 0, // set by caller
            importance: importance.clamp(0.0, 1.0),
            facts: Vec::new(),
            raw_content: raw,
        }
    }
}

/// Typed edge between atoms.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Edge {
    pub source: CID,
    pub target: CID,
    pub relation: String,
    pub weight: f32,
}

/// Simple bump allocator for ephemeral graph operations.
pub struct Arena<T> {
    storage: Vec<T>,
}

impl<T> Arena<T> {
    pub fn new() -> Self {
        Self { storage: Vec::new() }
    }

    pub fn with_capacity(cap: usize) -> Self {
        Self { storage: Vec::with_capacity(cap) }
    }

    pub fn alloc(&mut self, item: T) -> usize {
        let idx = self.storage.len();
        self.storage.push(item);
        idx
    }

    pub fn get(&self, idx: usize) -> Option<&T> {
        self.storage.get(idx)
    }

    pub fn get_mut(&mut self, idx: usize) -> Option<&mut T> {
        self.storage.get_mut(idx)
    }

    pub fn len(&self) -> usize {
        self.storage.len()
    }

    pub fn is_empty(&self) -> bool {
        self.storage.is_empty()
    }

    pub fn reset(&mut self) {
        self.storage.clear();
    }
}

impl<T> Default for Arena<T> {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cid_deterministic() {
        let c1 = CID::new(b"hello");
        let c2 = CID::new(b"hello");
        assert_eq!(c1, c2);
    }

    #[test]
    fn arena_alloc() {
        let mut arena = Arena::with_capacity(10);
        let idx = arena.alloc(42u32);
        assert_eq!(arena.get(idx), Some(&42));
    }

    #[test]
    fn atom_importance_clamped() {
        let a = Atom::new(vec![1, 2, 3], 1.5);
        assert_eq!(a.importance, 1.0);
    }
}