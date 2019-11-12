# Lint as: python2, python3
# Copyright 2018 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Common decoder interface."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections

from lingvo.core import base_layer
from lingvo.core import beam_search_helper
from lingvo.core import target_sequence_sampler

# metrics: Dict[Text, Tuple[float, float]] A dict of named metrics, which must
#   include 'loss'. The value of the dict is (metric_val, count), where
#   metric_val is the sum of the metric over all examples, and count is the
#   number of examples seen. The mean value of the metric is metric_val/count.
#   This is the first output of ComputeLoss.
# predictions: Union[Tensor, Dict[Text, Tensor], NestedMap] This is the output
#   of ComputePredictions.
# per_sequence: Dict[Text, Tensor] This is the second output of ComputeLoss.
DecoderOutput = collections.namedtuple(
    'DecoderOutput',
    ['metrics', 'predictions', 'per_sequence'],
)


class BaseDecoder(base_layer.BaseLayer):
  """Base class for all decoders."""

  @classmethod
  def Params(cls):
    p = super(BaseDecoder, cls).Params()
    p.Define(
        'packed_input', False, 'If True, decoder and all layers support '
        'multiple examples in a single sequence.')
    return p

  @classmethod
  def UpdateTargetVocabSize(cls, p, vocab_size, wpm_model=None):
    """Sets the vocab size and wpm model in the params.

    Args:
      p: model params.
      vocab_size: size of the vocabulary.
      wpm_model: file name prefix pointing to a wordpiece model.

    Returns:
      Model target vocabulary params updated with the vocab size and wpm model.
    """
    raise NotImplementedError('Abstract method')

  def FProp(self, theta, encoder_outputs, targets):
    """Decodes `targets` given encoded source.

    Args:
      theta: A `.NestedMap` object containing weights' values of this layer and
        its children layers.
      encoder_outputs: a NestedMap computed by encoder.
      targets: A NestedMap containing additional inputs to the decoder,
        such as the targets being predicted.

    Returns:
      A DecoderOutput namedtuple.
    """
    predictions = self.ComputePredictions(theta, encoder_outputs, targets)
    metrics, per_sequence = self.ComputeLoss(theta, predictions, targets)
    return DecoderOutput(
        metrics=metrics, predictions=predictions, per_sequence=per_sequence)

  def ComputePredictions(self, theta, encoder_outputs, targets):
    raise NotImplementedError('Abstract method: %s' % type(self))

  def ComputeLoss(self, theta, predictions, targets):
    raise NotImplementedError('Abstract method: %s' % type(self))


class BaseBeamSearchDecoder(BaseDecoder):
  """Decoder that does beam search."""

  @classmethod
  def Params(cls):
    p = super(BaseBeamSearchDecoder, cls).Params()
    p.Define('target_sos_id', 1, 'Id of the target sequence sos symbol.')
    p.Define('target_eos_id', 2, 'Id of the target sequence eos symbol.')
    # TODO(rpang): remove target_seq_len and use beam_search.target_seq_len
    # instead.
    p.Define('target_seq_len', 0, 'Target seq length.')
    p.Define('beam_search', beam_search_helper.BeamSearchHelper.Params(),
             'BeamSearchHelper params.')
    p.Define('target_sequence_sampler',
             target_sequence_sampler.TargetSequenceSampler.Params(),
             'TargetSequenceSampler params.')
    return p

  @classmethod
  def UpdateTargetVocabSize(cls, p, vocab_size, wpm_model=None):
    """Sets the vocab size and wpm model in the params.

    Args:
      p: model params.
      vocab_size: size of the vocabulary.
      wpm_model: file name prefix pointing to a wordpiece model.

    Returns:
      Model target vocabulary params updated with the vocab size and wpm model.
    """
    raise NotImplementedError('Abstract method')

  @base_layer.initializer
  def __init__(self, params):
    super(BaseBeamSearchDecoder, self).__init__(params)
    p = self.params
    p.beam_search.target_seq_len = p.target_seq_len
    p.beam_search.target_sos_id = p.target_sos_id
    p.beam_search.target_eos_id = p.target_eos_id
    self.CreateChild('beam_search', p.beam_search)
    p.target_sequence_sampler.target_seq_len = p.target_seq_len
    p.target_sequence_sampler.target_sos_id = p.target_sos_id
    p.target_sequence_sampler.target_eos_id = p.target_eos_id
    self.CreateChild('target_sequence_sampler', p.target_sequence_sampler)

  def AddExtraDecodingInfo(self, encoder_outputs, targets):
    """Adds extra decoding information to encoded_outputs.

    Args:
      encoder_outputs: a NestedMap computed by encoder.
      targets: a NestedMap containing target input fields.

    Returns:
      encoder_ouputs with extra information used for decoding.
    """
    return encoder_outputs

  def BeamSearchDecode(self, encoder_outputs, num_hyps_per_beam_override=0):
    """Performs beam search based decoding.

    Args:
      encoder_outputs: the outputs of the encoder.
      num_hyps_per_beam_override: If set to a value <= 0, this parameter is
        ignored. If set to a value > 0, then this value will be used to override
        p.num_hyps_per_beam.

    Returns:
      `.BeamSearchDecodeOutput`, A namedtuple whose elements are tensors.
    """
    return self.BeamSearchDecodeWithTheta(self.theta, encoder_outputs,
                                          num_hyps_per_beam_override)

  def BeamSearchDecodeWithTheta(self,
                                theta,
                                encoder_outputs,
                                num_hyps_per_beam_override=0):
    return self.beam_search.BeamSearchDecode(theta, encoder_outputs,
                                             num_hyps_per_beam_override,
                                             self._InitBeamSearchStateCallback,
                                             self._PreBeamSearchStepCallback,
                                             self._PostBeamSearchStepCallback)

  def SampleTargetSequences(self, theta, encoder_outputs, random_seed):
    """Performs target sequence sampling.

    Args:
      theta: A NestedMap object containing weights' values of this layer and its
        children layers.
      encoder_outputs: a NestedMap computed by encoder.
      random_seed: a scalar int32 tensor representing the random seed.

    Returns:
      A NestedMap containing the following tensors

      - 'ids': [batch, max_target_length] of int32, representing the target
        sequence ids, not including target_sos_id, but maybe ending with
        target_eos_id if target_eos_id is sampled.
      - 'paddings': [batch, max_target_length] of 0/1, where 1 represents
        a padded timestep.
    """
    return self.target_sequence_sampler.Sample(
        theta, encoder_outputs, random_seed, self._InitBeamSearchStateCallback,
        self._PreBeamSearchStepCallback, self._PostBeamSearchStepCallback)