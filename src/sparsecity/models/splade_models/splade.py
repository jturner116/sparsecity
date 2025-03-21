import torch
import torch.nn as nn
import torch.nn.functional as F


class SparseEmbedModel(nn.Module):
    def __init__(self, transformer_model: nn.Module, embedding_dim: int = 128):
        super().__init__()
        self.model = transformer_model
        self.embedding_dim = embedding_dim
        self.projection = nn.Linear(transformer_model.config.hidden_size, embedding_dim)

    def forward(self, input_ids, attention_mask, top_k=64):
        # Step 1: Extract encodings and logits
        outputs = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            output_hidden_states=True,
            return_dict=True,
        )
        logits = outputs.logits  # [batch_size, seq_len, vocab_size]
        sequence_encodings = outputs.hidden_states[
            -1
        ]  # [batch_size, seq_len, hidden_size]

        activations = torch.log1p(F.relu(logits)) * attention_mask.unsqueeze(-1)
        sparse_vector = torch.max(activations, dim=1).values  # [batch_size, vocab_size]

        top_values, top_indices = torch.topk(
            sparse_vector, k=top_k, dim=-1
        )  # [batch_size, top_k]

        batch_size, seq_len, vocab_size = logits.shape
        batch_indices = torch.arange(batch_size).unsqueeze(1).expand(-1, top_k)
        selected_logits = logits[
            batch_indices, :, top_indices
        ]  # [batch_size, seq_len, top_k]
        attention_weights = F.softmax(
            selected_logits, dim=1
        )  # [batch_size, seq_len, top_k]

        contextual_embeddings = torch.bmm(
            attention_weights.transpose(1, 2), sequence_encodings
        )
        # [batch_size, top_k, hidden_size]

        contextual_embeddings = F.relu(self.projection(contextual_embeddings))
        # [batch_size, top_k, embedding_dim]

        return {
            "sparse_activations": sparse_vector,  # [batch_size, vocab_size]
            "activations": top_indices,  # [batch_size, top_k]
            "embeddings": contextual_embeddings,  # [batch_size, top_k, embedding_dim]
        }


class SpladeModel(nn.Module):
    """
    SPLADE model that works with any transformer-based masked language model. Must provide top_k.
    """

    def __init__(self, transformer_model: nn.Module):
        super().__init__()
        self.model = transformer_model

    def forward(self, input_ids, attention_mask, top_k=64):
        # Get MLM logits from transformer
        outputs = self.model(
            input_ids=input_ids, attention_mask=attention_mask, return_dict=True
        )
        logits = outputs.logits

        # SPLADE activation
        activations = torch.log1p(F.relu(logits)) * attention_mask.unsqueeze(-1)
        values = torch.amax(activations, dim=1)

        top_values, _ = torch.topk(values, k=top_k, dim=-1)
        threshold = top_values[..., -1, None]
        values = values * (values >= threshold)

        return values


class SpladeModel_NoTopK(nn.Module):
    """
    SPLADE model that works with any transformer-based masked language model. Does not use top-k masking.
    """

    def __init__(self, transformer_model: nn.Module):
        super().__init__()
        self.model = transformer_model

    def forward(self, input_ids, attention_mask):
        # Get MLM logits from transformer
        outputs = self.model(
            input_ids=input_ids, attention_mask=attention_mask, return_dict=True
        )
        logits = outputs.logits

        # SPLADE activation
        activations = torch.log1p(F.relu(logits)) * attention_mask.unsqueeze(-1)
        values = torch.amax(activations, dim=1)

        return values, logits
