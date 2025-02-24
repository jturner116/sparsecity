from typing import Dict, Optional
from jaxtyping import Float, Int
import torch
import torch.nn as nn
import torch.nn.functional as F


@torch.compile(mode="default")
def train_step(
    model: nn.Module,
    query_input_ids: torch.Tensor,
    query_attention_mask: torch.Tensor,
    doc_input_ids: torch.Tensor,
    doc_attention_mask: torch.Tensor,
    top_k: int,
    lambda_t_d: torch.Tensor,
    lambda_t_q: torch.Tensor,
    device: torch.device,
    temperature: torch.Tensor,
    mse_weight: torch.Tensor,
    teacher_scores: Optional[torch.Tensor] = None,
) -> Dict[str, torch.Tensor]:
    torch.compiler.cudagraph_mark_step_begin()
    model.train()
    # optimizer.zero_grad()

    batch_size = query_input_ids.shape[0]
    num_docs = doc_input_ids.shape[1]

    # Combine queries and documents into a single batch
    doc_input_ids_flat = doc_input_ids.reshape(-1, doc_input_ids.shape[-1])
    doc_attention_mask_flat = doc_attention_mask.reshape(
        -1, doc_attention_mask.shape[-1]
    )

    # Concatenate query and document inputs
    combined_input_ids = torch.cat([query_input_ids, doc_input_ids_flat])
    combined_attention_mask = torch.cat([query_attention_mask, doc_attention_mask_flat])

    # Single forward pass for both queries and documents
    combined_embeddings = model(
        input_ids=combined_input_ids,
        attention_mask=combined_attention_mask,
        top_k=top_k,
    )
    # Split the embeddings back into queries and documents
    query_embeddings = combined_embeddings[:batch_size]
    doc_embeddings = combined_embeddings[batch_size:].reshape(batch_size, num_docs, -1)

    scores = torch.sum(query_embeddings.unsqueeze(1) * doc_embeddings, dim=-1)
    scores = scores / temperature
    # Create labels (assuming first document is positive)
    labels = torch.zeros(batch_size, dtype=torch.long, device=device)

    # Compute losses
    triplet_loss = F.cross_entropy(scores, labels)

    # Compute regularization terms
    doc_flops = torch.sum(
        torch.abs(doc_embeddings.reshape(-1, doc_embeddings.shape[-1])), dim=-1
    ).mean()
    query_l1 = torch.sum(torch.abs(query_embeddings), dim=-1).mean()
    flops = lambda_t_d * doc_flops + lambda_t_q * query_l1

    # Compute anti-zero loss
    # anti_zero = torch.reciprocal(
    #     torch.sum(query_embeddings) ** 2 + 1e-8
    # ) + torch.reciprocal(torch.sum(doc_embeddings) ** 2 + 1e-8)
    query_sum = torch.sum(torch.abs(query_embeddings))  # L1 norm to avoid cancellation
    doc_sum = torch.sum(torch.abs(doc_embeddings))
    anti_zero = 0.1 * (
        torch.log1p(1.0 / (query_sum + 1e-4)) + torch.log1p(1.0 / (doc_sum + 1e-4))
    )
    teacher_pos = teacher_scores[:, 0]  # Positive teacher score
    teacher_neg = teacher_scores[:, 1:]  # Negative teacher scores
    student_pos = scores[:, 0]  # Positive student score
    student_neg = scores[:, 1:]  # Negative student scores

    teacher_margins = (
        teacher_pos.unsqueeze(1) - teacher_neg
    )  # shape: (batch_size, num_negatives)
    student_margins = (
        student_pos.unsqueeze(1) - student_neg
    )  # shape: (batch_size, num_negatives)
    margin_mse_loss = F.mse_loss(student_margins, teacher_margins)

    # Total loss
    total_loss = triplet_loss + flops + anti_zero + (mse_weight * margin_mse_loss)

    # Backward pass
    total_loss.backward()
    # optimizer.step()

    metrics = {}

    metrics["loss"] = total_loss
    metrics["triplet_loss"] = triplet_loss
    metrics["margin_mse_loss"] = margin_mse_loss
    metrics["flops_loss"] = flops
    metrics["anti_zero_loss"] = anti_zero

    metrics["query_sparsity"] = (query_embeddings == 0).float().mean()
    metrics["doc_sparsity"] = (doc_embeddings == 0).float().mean()

    query_non_zero_vals = query_embeddings[query_embeddings != 0]
    doc_non_zero_vals = doc_embeddings[doc_embeddings != 0]

    metrics["query_min_non_zero"] = (
        query_non_zero_vals.abs().min()
        if query_non_zero_vals.numel() > 0
        else torch.tensor(0.0, device=device)
    )
    metrics["doc_min_non_zero"] = (
        doc_non_zero_vals.abs().min()
        if doc_non_zero_vals.numel() > 0
        else torch.tensor(0.0, device=device)
    )

    metrics["query_non_zero_count"] = query_non_zero_vals.numel()
    metrics["doc_non_zero_count"] = doc_non_zero_vals.numel()

    return metrics
