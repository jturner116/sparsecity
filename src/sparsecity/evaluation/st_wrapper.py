import torch
from torch import nn


class ST_SPLADEModule(nn.Module):
    def __init__(self, splade_model, tokenizer, max_length=256):
        super().__init__()
        self.splade_model = splade_model
        self.tokenizer = tokenizer
        self.max_length = max_length

    def forward(self, features: dict) -> dict:
        """
        The forward pass receives a features dict (as produced by tokenize) and returns
        a dictionary with the key "sentence_embedding".
        """
        model_inputs = {
            "input_ids": features["input_ids"],
            "attention_mask": features["attention_mask"],
        }
        # Ensure inputs are on the same device as the model.

        device = next(self.splade_model.parameters()).device
        model_inputs = {key: value.to(device) for key, value in model_inputs.items()}

        # Set the model to eval mode and get the embeddings.
        self.splade_model.eval()
        with torch.inference_mode():
            output = self.splade_model(**model_inputs)
            # In case your model returns a tuple, use the first element.
            if isinstance(output, (tuple, list)):
                output = output[0]
        # Store the output in the features dict under "sentence_embedding"
        # (Optionally move back to CPU)
        features["sentence_embedding"] = output.cpu()
        return features

    def tokenize(self, texts):
        """
        This method will be called by SentenceTransformer.encode() to tokenize raw texts.
        """
        return self.tokenizer(
            texts,
            padding="max_length",
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )

    def encode(self, texts, batch_size=32, **kwargs):
        """Produces sparse embeddings compatible with SentenceTransformer"""
        inputs = self.tokenizer(
            texts,
            padding="max_length",
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        ).to(self.splade.device)

        with torch.inference_mode():
            outputs = self.splade_model(**inputs)
        return outputs["doc_embedding"].cpu().numpy()


class ST_SPLADEV3Module(nn.Module):
    def __init__(self, splade_model, tokenizer, max_length=256):
        """
        :param splade_model: A SPLADE-v3 model loaded with return_dict=True.
        :param tokenizer: The corresponding AutoTokenizer.
        :param max_length: Maximum sequence length for tokenization.
        """
        super().__init__()
        self.splade_model = splade_model
        self.tokenizer = tokenizer
        self.max_length = max_length

    def forward(self, features: dict) -> dict:
        """
        Performs inference on a batch and returns a dict with key "sentence_embedding".

        The inference logic is:
         1. Tokenize inputs are already provided via `features`.
         2. Compute the logits from the model.
         3. Activate them with torch.relu followed by torch.log1p.
         4. Multiply by the attention mask and perform max pooling over the sequence dimension.
        """
        model_inputs = {
            "input_ids": features["input_ids"],
            "attention_mask": features["attention_mask"],
        }
        device = next(self.splade_model.parameters()).device
        model_inputs = {k: v.to(device) for k, v in model_inputs.items()}

        self.splade_model.eval()
        with torch.inference_mode():
            outputs = self.splade_model(**model_inputs)
            # Get raw logits from the masked LM head.
            logits = outputs.logits
            # Apply activation: make logits non-negative and compress scale.
            activated = torch.log1p(torch.relu(logits))
            # Multiply by attention mask to zero-out padding positions.
            # Then, perform max pooling along the sequence dimension.
            doc_reps, _ = torch.max(
                activated * model_inputs["attention_mask"].unsqueeze(-1), dim=1
            )

        features["sentence_embedding"] = doc_reps.cpu()
        return features

    def tokenize(self, texts):
        """
        Tokenizes raw texts for the SPLADE-v3 model.
        """
        return self.tokenizer(
            texts,
            padding="max_length",
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )

    def encode(self, texts, batch_size=32, **kwargs):
        """
        Produces sparse embeddings for a list of texts.
        This method can be used directly when you need to encode a batch.
        """
        inputs = self.tokenizer(
            texts,
            padding="max_length",
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        ).to(next(self.splade_model.parameters()).device)

        with torch.no_grad():
            outputs = self.splade_model(**inputs)
            logits = outputs.logits
            activated = torch.log1p(torch.relu(logits))
            doc_reps, _ = torch.max(
                activated * inputs["attention_mask"].unsqueeze(-1), dim=1
            )
        return doc_reps.cpu().numpy()
