import os
import pickle
import random
import numpy as np
import pandas as pd
from tqdm.auto import tqdm
from collections import defaultdict
from torch.utils.data import Dataset
from typing import Dict, Optional, Union, Tuple, List
import torch

from transformers import T5TokenizerFast

tokenizer = T5TokenizerFast.from_pretrained('t5-small',use_fast=True)

def _tokenize( x):
    return tokenizer(x, return_tensors="pt")['input_ids'][0][:-1]

def trun_pad(x):
  idx = x.nonzero()
  max_len = idx[:, 0].max() + 1
  return x[:max_len]

class KGCDataset(Dataset):
    def __init__(self, config, split="train"):
        self.config = config
        self.is_legacy = config.dataset.is_legacy
        self.split = split
        self.drop_subject_percentage = self.config.train.drop_subject
        if self.split != "train":
            self.drop_subject_percentage = 0.0
        self.dataset_name = self.config.dataset.name
        self.dataset_folder = os.path.join('data', self.dataset_name)
        print('Loading dataset {}, split {}'.format(self.dataset_name, split))
        print("loading entity and relation aliases")
        # self.ent_aliases, self.rel_aliases = self.get_ent_rel_alias_dicts(
        #     self.dataset_name
        # )
        import torch
        self.ent_aliases = torch.load('/content/kgt5-context/ent_tokenized_data.pt')
        self.rel_aliases = torch.load('/content/kgt5-context/rel_tokenized_data.pt')
        self.num_entities = len(self.ent_aliases)
        self.num_relations = len(self.rel_aliases)
        print("loading triples")
        # self.triples = dict()
        # for split in ["train", "valid", "test"]:
        #     self.triples[split] = self.load_triples_with_rev(split)
        # print("loading triples")
        # if self.config.valid.tiny:
        #     self.triples["valid_tiny"] = self.load_triples_with_rev("valid_tiny")
        self.triples = torch.load('/content/kgt5-context/triples.pt')
        self.data = self.get_split(self.split)
        self.sep = _tokenize('|')
        self.newline = _tokenize('<extra_id_10>')
        self.query_tokens = _tokenize('<extra_id_10>')
        self.context_tokens = _tokenize('context:')
        
        self.use_desc = self.config.descriptions.use
        if self.use_desc:
            print("loading descriptions")
            self.description_separator = _tokenize("<extra_id_96>")
            self.ent_descriptions = self.load_descriptions(self.dataset_name)

        self._filter_dict = None

    @property
    def filter_dict(self):
        if self._filter_dict is None:
            print("create filter dict for evaluation")
            self._filter_dict = self.create_filter()
        return self._filter_dict

    def __len__(self):
        return len(self.data)

    def load_descriptions(self, dataset_name):
        desc_fname = os.path.join('data', dataset_name, 'entity_desc.del')
        return self.load_aliases(desc_fname)

    @staticmethod
    def create(config, split="train"):
        if config.dataset.v1:
            return KGCV1Dataset(config=config, split=split)
        else:
            return KGCContextDataset(config=config, split=split)

    def get_split(self, split: str):
        return self.triples[split]

    @staticmethod
    def load_aliases(fname: str) -> Dict:
        pickle_file_name = os.path.splitext(fname)[0] + ".pckl"
        if os.path.exists(pickle_file_name):
            with open(pickle_file_name, "rb") as f:
                out_dict = pickle.load(f)
                return out_dict
        out_dict = {}
        with open(fname, "r", encoding="utf-8") as f:
            for line in f:
                if line[-1] == '\n':
                    line = line[:-1]
                id, name = line.split('\t')
                id = int(id)
                out_dict[id] = name
        with open(pickle_file_name, "wb") as f:
            pickle.dump(out_dict, f)
        return out_dict

    @staticmethod
    def load_aliases_list(fname: str) -> Dict:
        pickle_file_name = os.path.splitext(fname)[0] + ".pckl"
        if os.path.exists(pickle_file_name):
            with open(pickle_file_name, "rb") as f:
                out_dict = pickle.load(f)
                return out_dict
        out_dict = {}
        with open(fname, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if line[-1] == '\n':
                    line = line[:-1]
                id = int(line)
                out_dict[i] = id
        with open(pickle_file_name, "wb") as f:
            pickle.dump(out_dict, f)
        return out_dict

    @staticmethod
    def load_triples(fname: str) -> np.array:
        pickle_file_name = os.path.splitext(fname)[0] + ".npy"
        if os.path.exists(pickle_file_name):
            triples = np.load(pickle_file_name)
            return triples
        triples = pd.read_csv(fname, delimiter="\t", header=None).to_numpy()
        np.save(pickle_file_name, triples)
        return triples

    def get_ent_rel_alias_dicts(self, dataset_name: str) -> Tuple[Dict, Dict]:
        ent_fname = os.path.join('data', dataset_name, 'entity_mentions.del')
        rel_fname = os.path.join('data', dataset_name, 'relation_mentions.del')
        ent_dict = self.load_aliases(ent_fname)
        rel_dict = self.load_aliases(rel_fname)
        return ent_dict, rel_dict

    def load_triples_with_rev(self, split: str) -> np.array:
        file_name = os.path.join(self.dataset_folder, f"{split}.del")
        triples = self.load_triples(file_name)
        rev_triples = np.empty_like(triples)
        rev_triples[:, 0] = triples[:, 2]
        rev_triples[:, 2] = triples[:, 0]
        rev_triples[:, 1] = triples[:, 1] + self.num_relations
        return np.concatenate((triples, rev_triples), axis=0)

    def create_filter(self, splits: Union[List, Tuple] = ["train", "valid", "test"]):
        filter_dict = defaultdict(list)
        for split in splits:
            print("creating filter dict for split", split)
            for triple in tqdm(self.get_split(split)):
                filter_dict[(triple[0], triple[1])].append(self.ent_aliases[triple[2]])
        return filter_dict


class KGCContextDataset(KGCDataset):
    def __init__(self, config, split="train"):
        super().__init__(config=config, split=split)
        self.max_context_size = self.config.context.max_size
        self.use_context = self.config.context.use
        self.context_separator = _tokenize("<extra_id_98>")
        if self.is_legacy:
            self.context_separator = "\n"
        self.drop_mask_token = _tokenize("<extra_id_99>")
        self.context_hop_separator = _tokenize("<extra_id_97>")
        print("creating neighborhood indexes")
        self.hop1_index = Hop1Index(
            self.config, self.get_split("train"), self.num_entities
        )

        print('Loaded dataset')

    def get_context(
            self,
            subject: int,
            predicate: Optional[int] = None,
            obj: Optional[int] = None
    ) -> np.array:
        context_triples = self.hop1_index[subject]
        if predicate is not None and obj is not None:
            filter_mask = np.logical_and(
                context_triples[:, 0] == predicate, context_triples[:, 1] == obj
            )
            context_triples = context_triples[~filter_mask]
        return context_triples  # .tolist()

    def create_query_string(self, triple, split=None):
        if split is None:
            split = self.split
        if random.random() >= self.drop_subject_percentage:
            source = [
                self.query_tokens,
                trun_pad(self.ent_aliases[triple[0]]),
                self.sep,
                self.rel_aliases[triple[1]],
                self.newline]
        else:
            source = [
                self.query_tokens,
                self.drop_mask_token,
                self.sep,
                self.rel_aliases[triple[1]],
                self.newline]
        if self.use_desc:
            source += f" {self.description_separator} {self.ent_descriptions[triple[0]]} "
        return source

    def create_query_string_no_context(self, triple, split=None):
        if split is None:
            split = self.split
        
        source = [
          trun_pad(self.ent_aliases[triple[0]]),
          self.sep,
          self.rel_aliases[triple[1]]]
        return source

    def triple_context_to_source_target(self, triple, context_list, split=None):
        
        target = trun_pad(self.ent_aliases[triple[2]])
        if self.use_context:
            source = self.create_query_string(triple, split=split)
        else:
            source = self.create_query_string_no_context(triple, split=split)
            return source, target
        source.append(self.context_tokens)
        len_source = sum([s.shape[0] for s in source])
      
        context_size = 0
        for p, o in context_list:
            if p == triple[1] and o == triple[2]:
                continue
            p = trun_pad(self.rel_aliases[p])
            o = trun_pad(self.ent_aliases[o])
            next_context = [
                self.context_separator,
                p, self.sep, o
            ]
            len_next_context = sum([s.shape[0] for s in next_context])
            if len_source + len_next_context > self.config.model.max_input_length:
                break
            source.extend(next_context)
            len_source += len_next_context
            
        return source, target

    def __getitem__(self, idx):
        return self.get(idx, split=self.split)

    def get(self, idx: int, split: str = "train") -> Dict:
        triple = self.triples[split][idx]
        # context_list = self.get_context(triple[0], triple[1], triple[2])
        context_list = None
        source, target = self.triple_context_to_source_target(
            triple, context_list, split=split
        )
        source = torch.cat(source)
        is_tail_pred = triple[1] < self.num_relations
        output = {
            "input": source,
            "target": target,
            "query": (triple[0], triple[1]),
            "is_tail_pred": is_tail_pred
        }
        return output


class KGCV1Dataset(KGCDataset):
    def __init__(self, config, split):
        super().__init__(config=config, split=split)
        self.tail_pred_token = _tokenize("<extra_id_55>")
        self.head_pred_token = _tokenize("<extra_id_56>")

    def get_source_and_target(self, triple):
        is_reverse = triple[1] >= self.num_relations
        if is_reverse:
            source = f"{self.head_pred_token} {self.ent_aliases[triple[0]]} | {self.rel_aliases[triple[1]-self.num_relations]} | "
            if self.is_legacy:
                source = f"|HEAD| {self.ent_aliases[triple[0]]}||| {self.rel_aliases[triple[1]-self.num_relations]}"
        else:
            source = f"{self.tail_pred_token} {self.ent_aliases[triple[0]]} | {self.rel_aliases[triple[1]]} | "
            if self.is_legacy:
                source = f"|TAIL| {self.ent_aliases[triple[0]]}||| {self.rel_aliases[triple[1]]}"
        target = self.ent_aliases[triple[2]]
        if self.use_desc:
            source += f" {self.description_separator} {self.ent_descriptions[triple[0]]} "
        return source, target

    def get(self, idx, split="train"):
        triple = self.get_split(split)[idx]
        source, target = self.get_source_and_target(triple)
        is_tail_pred = triple[1] < self.num_relations
        output = {
            "input": source,
            "target": target,
            "query": (triple[0], triple[1]),
            "is_tail_pred": is_tail_pred
        }
        return output



class SplitDatasetWrapper:
    def __init__(self, dataset, split="train"):
        self.dataset = dataset
        self.split = split

    def __getitem__(self, idx):
        return self.dataset.get(idx, self.split)

    def __len__(self):
        return len(self.dataset.get_split(split=self.split))


class Hop1Index:
    def __init__(self, config, triples, num_entities, key_col=0):
        self.config = config
        self.max_context_size = self.config.context.max_size
        self.shuffle = self.config.context.shuffle
        self.triples = np.copy(triples[triples[:, key_col].argsort()])
        keys, values_offset = np.unique(
            self.triples[:, key_col], axis=0, return_index=True
        )
        values_offset = np.append(values_offset, len(self.triples))
        self.keys = keys
        self.values_offset = values_offset
        self.key_to_start = np.full([num_entities,], -1)
        self.key_to_start[keys] = self.values_offset[:-1]
        self.key_to_end = np.full([num_entities,], -1)
        self.key_to_end[keys] = self.values_offset[1:]

    def __getitem__(self, item):
        start = self.key_to_start[item]
        end = self.key_to_end[item]
        context = self.triples[start:end, [1, 2]]
        if len(context) > self.max_context_size:
          ids = np.random.choice(len(context), self.max_context_size, replace=False)
          context = context[ids]
        if self.shuffle:
            # context = np.copy(context)
            np.random.shuffle(context)
        # if end - start > self.max_context_size:
        # context = context[:self.max_context_size]
        return context

    def get(self, item):
        return self[item]

        return context

    def get(self, item):
        return self[item]
